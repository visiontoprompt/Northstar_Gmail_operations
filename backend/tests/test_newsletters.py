import os
import hashlib

os.environ["DATABASE_URL"] = "sqlite:///./data/test_gmail_ops.db"
os.environ["SECRET_KEY"] = "test-secret-key-without-credential-material"
os.environ["REDIS_URL"] = "fakeredis://"

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from types import SimpleNamespace
from unittest.mock import patch

from app.db import Base, SessionLocal, engine
from app.schemas import NewsletterJobCreate
from app.main import _newsletter_discovery_outcome, app, settings
from app.models import (
    AccountGroup,
    AuditEvent,
    BrowserProfile,
    BrowserSession,
    GmailAccount,
    Newsletter,
    NewsletterAutomationProfile,
    NewsletterGroup,
    NewsletterJob,
    NewsletterSubscription,
    NewsletterTask,
    NewsletterTaskEvent,
    Organization,
    utcnow,
)
from app.newsletter_service import (
    NewsletterValidationError,
    create_newsletter,
    create_newsletter_group,
    create_newsletter_job,
    delete_newsletter_job_history,
    delete_newsletter_job_histories,
    create_newsletter_profile,
    dispatch_newsletter_job,
    evaluate_newsletter_automation_health,
    get_newsletter,
    get_newsletter_group,
    get_newsletter_job,
    get_newsletter_profile_by_id,
    get_newsletter_subscription,
    list_newsletter_groups,
    list_newsletter_jobs,
    list_newsletter_profiles,
    list_newsletters,
    list_newsletter_subscriptions,
    materialize_newsletter_job,
    newsletter_automation_health_response,
    import_newsletter_catalog,
    record_newsletter_discovery,
    recover_stale_newsletter_tasks,
    _newsletter_queue_entries,
    _newsletter_resource_aware_limit,
    _job_targets,
    promote_newsletter_profile,
    reject_newsletter_profile,
    rollback_newsletter_profile,
    seed_newsletter_directory,
    update_newsletter_profile_by_id,
    NEWSLETTER_DIRECTORY_GROUP_NAMES,
)
from app.worker import redis as worker_redis
from app.service import _publisher_signup_password
from app.service import _maybe_dispatch_newsletter_next


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    worker_redis.reset_redis()
    worker_redis.flush_redis()
    with TestClient(app):
        pass


def _org(db: Session, org_id: str, name: str = "Org") -> Organization:
    org = Organization(id=org_id, name=name)
    db.add(org)
    db.commit()
    return org


def _account_group(db: Session, org_id: str, name: str = "Account Pool") -> AccountGroup:
    group = AccountGroup(id=f"agroup_{org_id}_{name[:20]}", organization_id=org_id, name=name, description=None, capacity=300, status="ACTIVE")
    db.add(group)
    db.commit()
    return group


def _account(db: Session, org_id: str, group: AccountGroup, email: str) -> GmailAccount:
    account = GmailAccount(
        id=f"acct_{org_id}_{email}",
        organization_id=org_id,
        group_id=group.id,
        email=email,
        normalized_email=email.lower(),
        account_type="GMAIL",
    )
    db.add(account)
    db.commit()
    return account


def _newsletter_group(db: Session, org_id: str, name: str = "Technology") -> NewsletterGroup:
    group = NewsletterGroup(id=f"nlngroup_{org_id}_{name}", organization_id=org_id, name=name, description=None, status="ACTIVE")
    db.add(group)
    db.commit()
    return group


def _newsletter(db: Session, org_id: str, newsletter_group: NewsletterGroup, name: str = "Daily Digest") -> Newsletter:
    newsletter = Newsletter(
        id=f"nlnews_{org_id}_{name}",
        organization_id=org_id,
        group_id=newsletter_group.id,
        name=name,
        website_url="https://example.com",
        signup_url="https://example.com/signup",
        status="ACTIVE",
        confirmation_required=True,
        discovery_status="NOT_DISCOVERED",
    )
    db.add(newsletter)
    db.commit()
    return newsletter


def test_delete_bulk_history_preserves_subscription_registry() -> None:
    with SessionLocal() as db:
        org = _org(db, "delete_history_org")
        account_group = _account_group(db, org.id)
        newsletter_group = _newsletter_group(db, org.id)
        newsletter = _newsletter(db, org.id, newsletter_group)
        account = _account(db, org.id, account_group, "reader@example.com")
        subscription = NewsletterSubscription(id="delete_history_subscription", organization_id=org.id, account_id=account.id, newsletter_id=newsletter.id, state="CONFIRMED")
        db.add(subscription)
        db.commit()
        job = create_newsletter_job(db, org.id, "admin", newsletter.id, None, account_group.id, 1, {}, False)
        delete_newsletter_job_history(db, org.id, "admin", job["id"])
        assert get_newsletter_job(db, org.id, job["id"]) is None
        assert db.get(NewsletterSubscription, subscription.id).state == "CONFIRMED"


def test_bulk_delete_job_histories_is_atomic_and_preserves_active_runs() -> None:
    with SessionLocal() as db:
        org = _org(db, "bulk_delete_jobs_org")
        account_group = _account_group(db, org.id)
        newsletter_group = _newsletter_group(db, org.id)
        newsletter = _newsletter(db, org.id, newsletter_group)
        finished = create_newsletter_job(db, org.id, "admin", newsletter.id, None, account_group.id, 1, {}, False)
        active = create_newsletter_job(db, org.id, "admin", newsletter.id, None, account_group.id, 1, {}, False)
        active_row = db.get(NewsletterJob, active["id"])
        active_row.status = "RUNNING"
        db.commit()

        try:
            delete_newsletter_job_histories(db, org.id, "admin", [finished["id"], active["id"]])
            assert False, "bulk deletion must reject active jobs"
        except NewsletterValidationError as exc:
            assert "Stop newsletter job" in str(exc)

        assert get_newsletter_job(db, org.id, finished["id"]) is not None
        assert get_newsletter_job(db, org.id, active["id"]) is not None
        assert delete_newsletter_job_histories(db, org.id, "admin", [finished["id"]]) == 1
        assert get_newsletter_job(db, org.id, finished["id"]) is None


def test_newsletter_signup_score_uses_account_outcomes() -> None:
    with SessionLocal() as db:
        _org(db, "score_org")
        account_group = _account_group(db, "score_org")
        newsletter_group = _newsletter_group(db, "score_org")
        newsletter = _newsletter(db, "score_org", newsletter_group)
        for index, state in enumerate(("CONFIRMED", "WAITING_CONFIRMATION", "MANUAL_REVIEW")):
            account = _account(db, "score_org", account_group, f"reader{index}@example.com")
            db.add(NewsletterSubscription(
                id=f"score_subscription_{index}", organization_id="score_org",
                account_id=account.id, newsletter_id=newsletter.id, state=state,
            ))
        db.commit()

        result = get_newsletter(db, "score_org", newsletter.id)
        assert result["signup_score"] == 50
        assert result["subscribed_count"] == 2
        assert result["confirmed_count"] == 1
        assert result["review_count"] == 1
        assert result["scored_count"] == 3
        assert list_newsletters(db, "score_org")[0]["signup_score"] == 50


def test_publisher_password_is_encrypted_per_subscription() -> None:
    subscription = NewsletterSubscription(id="secret_row", organization_id="org", account_id="account", newsletter_id="newsletter")
    assert _publisher_signup_password(subscription, "Unique publisher passphrase") == "Unique publisher passphrase"
    assert subscription.signup_password_encrypted != "Unique publisher passphrase"
    assert "Unique publisher passphrase" not in subscription.signup_password_encrypted
    assert _publisher_signup_password(subscription) == "Unique publisher passphrase"


def test_newsletter_job_rejects_more_than_ten_concurrent_sessions() -> None:
    assert NewsletterJobCreate(newsletter_id="newsletter", account_group_id="accounts", concurrency_limit=10).concurrency_limit == 10
    try:
        NewsletterJobCreate(newsletter_id="newsletter", account_group_id="accounts", concurrency_limit=11)
    except ValidationError:
        pass
    else:
        raise AssertionError("Newsletter bulk concurrency above 10 was accepted")


def test_newsletter_directory_seed_is_idempotent_and_preserves_legacy_sport_alias() -> None:
    db = SessionLocal()
    try:
        org = _org(db, "org_directory")
        legacy = NewsletterGroup(id="nlngroup_legacy_sport", organization_id=org.id, name="sport", description=None, status="ACTIVE")
        db.add(legacy)
        db.commit()

        first = seed_newsletter_directory(db, org.id, "admin_demo")
        assert first["requested_count"] == len(NEWSLETTER_DIRECTORY_GROUP_NAMES) == 80
        assert first["created_count"] == 79
        assert {row.name.casefold() for row in db.query(NewsletterGroup).filter(NewsletterGroup.organization_id == org.id).all()}.__len__() == 80
        assert {item["existing"] for item in first["preserved_aliases"]} == {"sport"}

        second = seed_newsletter_directory(db, org.id, "admin_demo")
        assert second["created_count"] == 0
        assert second["existing_count"] == 80
        assert db.query(NewsletterGroup).filter(NewsletterGroup.organization_id == org.id).count() == 80
    finally:
        db.close()


def test_supplied_newsletter_catalog_import_is_grouped_and_idempotent() -> None:
    db = SessionLocal()
    try:
        org = _org(db, "org_catalog")
        first = import_newsletter_catalog(db, org.id, "admin_demo")
        assert first["created_groups"] == 5
        assert first["created_count"] > 250
        assert first["skipped_count"] >= 1
        imported_groups = {
            row.name
            for row in db.query(NewsletterGroup).filter(NewsletterGroup.organization_id == org.id).all()
        }
        assert imported_groups == {
            "News & Public Affairs",
            "Technology & Business",
            "Science, Health & Education",
            "Lifestyle, Travel & Culture",
            "Shopping, Food & Brands",
        }
        imported = db.query(Newsletter).filter(Newsletter.organization_id == org.id).all()
        assert imported
        assert all(item.status == "TEST_REQUIRED" for item in imported)
        assert all(item.discovery_status == "NOT_DISCOVERED" for item in imported)

        second = import_newsletter_catalog(db, org.id, "admin_demo")
        assert second["created_groups"] == 0
        assert second["created_count"] == 0
        assert second["existing_count"] == first["created_count"]
        assert db.query(Newsletter).filter(Newsletter.organization_id == org.id).count() == len(imported)
    finally:
        db.close()


def _profile(db: Session, org_id: str, newsletter: Newsletter, version: int, status: str = "TEST_PASSED") -> NewsletterAutomationProfile:
    profile = NewsletterAutomationProfile(
        id=f"nlprofile_{org_id}_{newsletter.id}_v{version}",
        organization_id=org_id,
        newsletter_id=newsletter.id,
        version=version,
        status=status,
        configuration_json={"email_selector": f"input[type=email]_v{version}"},
        confidence=0.9,
    )
    db.add(profile)
    db.commit()
    return profile


# ---------------------------------------------------------------------------
# Newsletter groups (API)
# ---------------------------------------------------------------------------


def test_newsletter_group_crud_via_api() -> None:
    with TestClient(app) as client:
        created = client.post("/api/v1/newsletter-groups", json={"name": "Technology", "description": "Tech sources"})
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "ACTIVE"
        assert body["newsletter_count"] == 0
        group_id = body["id"]

        duplicate = client.post("/api/v1/newsletter-groups", json={"name": "Technology"})
        assert duplicate.status_code == 409

        updated = client.patch(f"/api/v1/newsletter-groups/{group_id}", json={"name": "Tech", "description": "Renamed"})
        assert updated.status_code == 200
        assert updated.json()["name"] == "Tech"

        disabled = client.post(f"/api/v1/newsletter-groups/{group_id}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "DISABLED"

        enabled = client.post(f"/api/v1/newsletter-groups/{group_id}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "ACTIVE"

        detail = client.get(f"/api/v1/newsletter-groups/{group_id}")
        assert detail.status_code == 200
        assert detail.json()["name"] == "Tech"

        listed = client.get("/api/v1/newsletter-groups")
        assert listed.status_code == 200
        assert any(item["id"] == group_id for item in listed.json())

        assert client.get("/api/v1/newsletter-groups/group_missing").status_code == 404


# ---------------------------------------------------------------------------
# Newsletters (API + organization scope)
# ---------------------------------------------------------------------------


def _create_api_group(client: TestClient) -> str:
    return client.post("/api/v1/newsletter-groups", json={"name": "Business"}).json()["id"]


def test_newsletter_crud_via_api() -> None:
    with TestClient(app) as client:
        group_id = _create_api_group(client)
        payload = {
            "group_id": group_id,
            "name": "Market Brief",
            "website_url": "https://market.example.com",
            "signup_url": "https://market.example.com/subscribe",
            "confirmation_required": True,
            "allowed_confirmation_domains": ["market.example.com", "https://confirm.market.example.com/"],
            "sender_patterns": ["Market Brief <news@market.example.com>"],
            "subject_patterns": ["Confirm your subscription"],
        }
        created = client.post("/api/v1/newsletters", json=payload)
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "ACTIVE"
        assert body["confirmation_required"] is True
        assert "confirm.market.example.com" in body["allowed_confirmation_domains"]
        assert "market.example.com" in body["allowed_confirmation_domains"]
        assert "https://" not in "".join(body["allowed_confirmation_domains"])
        assert body["profile_versions"] == 0
        newsletter_id = body["id"]

        malformed = client.post("/api/v1/newsletters", json={"group_id": group_id, "name": "Bad", "website_url": "not-a-url"})
        assert malformed.status_code == 422
        bad_domain = client.post(
            "/api/v1/newsletters", json={"group_id": group_id, "name": "Bad2", "allowed_confirmation_domains": ["not a domain"]}
        )
        assert bad_domain.status_code == 422

        updated = client.patch(f"/api/v1/newsletters/{newsletter_id}", json={"status": "TEST_REQUIRED", "name": "Market Brief Daily"})
        assert updated.status_code == 200
        assert updated.json()["status"] == "TEST_REQUIRED"
        assert updated.json()["name"] == "Market Brief Daily"

        disabled = client.post(f"/api/v1/newsletters/{newsletter_id}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "DISABLED"

        enabled = client.post(f"/api/v1/newsletters/{newsletter_id}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "ACTIVE"

        detail = client.get(f"/api/v1/newsletters/{newsletter_id}")
        assert detail.status_code == 200
        assert client.get("/api/v1/newsletters").json()[0]["id"] == newsletter_id

        assert client.get("/api/v1/newsletters/nl_missing").status_code == 404


def test_newsletter_organization_scope() -> None:
    db: Session = SessionLocal()
    try:
        org_a = _org(db, "org_a", "A")
        org_b = _org(db, "org_b", "B")
        group_a = _newsletter_group(db, "org_a")
        _newsletter_group(db, "org_b", "Security")
        newsletter = _newsletter(db, "org_a", group_a)

        assert get_newsletter_group(db, "org_a", group_a.id) is not None
        assert get_newsletter_group(db, "org_b", group_a.id) is None
        # Same group name is legal in another organization.
        assert list_newsletter_groups(db, "org_b")[0]["name"] == "Security"

        assert get_newsletter(db, "org_b", newsletter.id) is None
        assert [item["id"] for item in list_newsletter_groups(db, "org_b")] != [group_a.id]

        too_many = False
        assert org_a.id and not too_many
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Automation profiles: versioning, promotion, rollback, rejection
# ---------------------------------------------------------------------------


def test_profile_versioning_promotion_rollback() -> None:
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        group = _newsletter_group(db, "org_a")
        newsletter = _newsletter(db, "org_a", group)

        p1 = create_newsletter_profile(db, "org_a", "admin_a", newsletter.id, "TEST_PASSED", 0.9, {"email_selector": "input#email"})
        assert p1["version"] == 1
        p2 = create_newsletter_profile(db, "org_a", "admin_a", newsletter.id, "TEST_PASSED", 0.95, {"email_selector": "input[name=email]"})
        assert p2["version"] == 2
        p3 = create_newsletter_profile(db, "org_a", "admin_a", newsletter.id, "DISCOVERED", 0.6, {"email_selector": "input[type=email]"})
        assert p3["version"] == 3

        # Versions are never overwritten.
        versions = [p["version"] for p in list_newsletter_profiles(db, "org_a", newsletter.id)]
        assert versions == [3, 2, 1]
        assert get_newsletter_profile_by_id(db, "org_a", p1["id"])["configuration"] == {"email_selector": "input#email"}

        # Promote v1 -> ACTIVE.
        promoted = promote_newsletter_profile(db, "org_a", "admin_a", newsletter.id, 1)
        assert promoted["status"] == "ACTIVE"
        assert get_newsletter(db, "org_a", newsletter.id)["active_profile_version"] == 1

        # Promote v2 -> previous ACTIVE becomes SUPERSEDED (same commit).
        promoted2 = promote_newsletter_profile(db, "org_a", "admin_a", newsletter.id, 2)
        assert promoted2["status"] == "ACTIVE"
        assert promoted2["version"] == 2
        assert get_newsletter_profile_by_id(db, "org_a", p1["id"])["status"] == "SUPERSEDED"
        assert get_newsletter(db, "org_a", newsletter.id)["active_profile_version"] == 2

        # Invalid promotion: DISCOVERED cannot become ACTIVE.
        try:
            promote_newsletter_profile(db, "org_a", "admin_a", newsletter.id, 3)
            assert False, "DISCOVERED profile should not be promotable"
        except NewsletterValidationError:
            pass

        # Reject a candidate; a REJECTED profile can never become active.
        rejected = reject_newsletter_profile(db, "org_a", "admin_a", newsletter.id, 3)
        assert rejected["status"] == "REJECTED"
        try:
            promote_newsletter_profile(db, "org_a", "admin_a", newsletter.id, 3)
            assert False, "REJECTED profile should not be promotable"
        except NewsletterValidationError:
            pass

        # Rollback to the SUPERSEDED v1.
        rolled = rollback_newsletter_profile(db, "org_a", "admin_a", newsletter.id, 1)
        assert rolled["status"] == "ACTIVE"
        assert rolled["version"] == 1
        assert get_newsletter_profile_by_id(db, "org_a", p2["id"])["status"] == "SUPERSEDED"

        # Rejecting an ACTIVE profile is not a valid transition.
        try:
            reject_newsletter_profile(db, "org_a", "admin_a", newsletter.id, 1)
            assert False, "ACTIVE profile should not be rejectable"
        except NewsletterValidationError:
            pass
    finally:
        db.close()


def test_profile_duplicate_version_constraint_blocks_overwrite() -> None:
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        group = _newsletter_group(db, "org_a")
        newsletter = _newsletter(db, "org_a", group)
        _profile(db, "org_a", newsletter, 1)
        too_many = False
        try:
            _profile(db, "org_a", newsletter, 1)
            assert False, "duplicate (newsletter_id, version) should violate the unique constraint"
        except IntegrityError:
            db.rollback()
        assert not too_many
    finally:
        db.close()


def test_profile_edit_publishes_a_new_candidate_version() -> None:
    """An operator's correction must not rewrite what was already tested.

    The reviewed version stays on the record; the fix becomes the next
    candidate, which the operator then signs up against.
    """
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        group = _newsletter_group(db, "org_a")
        newsletter = _newsletter(db, "org_a", group)
        source = create_newsletter_profile(db, "org_a", "admin_a", newsletter.id, "TEST_FAILED", 0.7, {"email_selector": "input#email"})

        updated = update_newsletter_profile_by_id(db, "org_a", "admin_a", source["id"], {"entry_selector": ".newsletter-card"})

        assert updated["version"] == 2
        assert updated["status"] == "CANDIDATE"
        assert updated["confidence"] == 0.7
        # The operator's key merges over the reviewed configuration.
        assert updated["configuration"] == {"email_selector": "input#email", "entry_selector": ".newsletter-card"}
        # The tested version is untouched.
        reviewed = get_newsletter_profile_by_id(db, "org_a", source["id"])
        assert reviewed["status"] == "TEST_FAILED"
        assert reviewed["configuration"] == {"email_selector": "input#email"}

        # An empty value clears the step again, so a card grid that starts
        # rendering its form directly can be put back.
        cleared = update_newsletter_profile_by_id(db, "org_a", "admin_a", updated["id"], {"entry_selector": ""})
        assert cleared["version"] == 3
        assert cleared["configuration"] == {"email_selector": "input#email"}

        # The credential boundary applies to this write path too.
        rejected = False
        try:
            update_newsletter_profile_by_id(db, "org_a", "admin_a", source["id"], {"account_password": "hunter2"})
        except NewsletterValidationError:
            rejected = True
            db.rollback()
        assert rejected
    finally:
        db.close()


def test_profile_edit_endpoint_creates_a_candidate_and_rejects_forbidden_keys() -> None:
    with TestClient(app) as client:
        group_id = _create_api_group(client)
        newsletter_id = client.post(
            "/api/v1/newsletters", json={"group_id": group_id, "name": "Two Step Digest", "signup_url": "https://digest.example.com/newsletters"}
        ).json()["id"]
        profile = client.post(
            "/api/v1/newsletter-profiles",
            json={"newsletter_id": newsletter_id, "status": "TEST_FAILED", "confidence": 0.6, "configuration": {"email_selector": "input#email"}},
        ).json()

        response = client.patch(f"/api/v1/newsletter-profiles/{profile['id']}", json={"configuration": {"entry_selector": ".newsletter-card"}})

        assert response.status_code == 200
        body = response.json()
        assert body["version"] == 2
        assert body["status"] == "CANDIDATE"
        assert body["configuration"]["entry_selector"] == ".newsletter-card"
        assert body["configuration"]["email_selector"] == "input#email"

        forbidden = client.patch(f"/api/v1/newsletter-profiles/{profile['id']}", json={"configuration": {"session_cookie": "abc"}})
        assert forbidden.status_code == 422

        missing = client.patch("/api/v1/newsletter-profiles/nlprofile_missing", json={"configuration": {"entry_selector": ".card"}})
        assert missing.status_code == 404


def test_profile_endpoints_via_api() -> None:
    with TestClient(app) as client:
        group_id = _create_api_group(client)
        newsletter_id = client.post(
            "/api/v1/newsletters", json={"group_id": group_id, "name": "Profile Digest", "signup_url": "https://digest.example.com/signup"}
        ).json()["id"]

        first = client.post(
            "/api/v1/newsletter-profiles",
            json={"newsletter_id": newsletter_id, "status": "TEST_PASSED", "confidence": 0.9, "configuration": {"email_selector": "input#email"}},
        )
        assert first.status_code == 201
        assert first.json()["version"] == 1
        second = client.post(
            "/api/v1/newsletter-profiles",
            json={"newsletter_id": newsletter_id, "status": "TEST_PASSED", "confidence": 0.95, "configuration": {"email_selector": "input[name*email]"}},
        )
        assert second.json()["version"] == 2

        versions = client.get(f"/api/v1/newsletter-profiles?newsletter_id={newsletter_id}")
        assert versions.status_code == 200
        assert [p["version"] for p in versions.json()] == [2, 1]

        detail = client.get(f"/api/v1/newsletter-profiles/{first.json()['id']}")
        assert detail.status_code == 200
        assert detail.json()["configuration"]["email_selector"] == "input#email"

        promoted = client.post(f"/api/v1/newsletter-profiles/{second.json()['id']}/promote")
        assert promoted.status_code == 200
        assert promoted.json()["status"] == "ACTIVE"

        health = client.get(f"/api/v1/newsletters/{newsletter_id}/health")
        assert health.status_code == 200
        assert health.json()["bulk_blocked"] is False
        assert health.json()["active_profile_version"] == 2

        assert get_newsletter(db=SessionLocal(), organization_id=settings.demo_org_id, newsletter_id=newsletter_id)["active_profile_version"] == 2

        superseded = client.get(f"/api/v1/newsletter-profiles/{first.json()['id']}").json()
        assert superseded["status"] == "SUPERSEDED"

        rolled = client.post(f"/api/v1/newsletter-profiles/{first.json()['id']}/rollback")
        assert rolled.status_code == 200
        assert rolled.json()["status"] == "ACTIVE"

        secret = client.post(
            "/api/v1/newsletter-profiles",
            json={"newsletter_id": newsletter_id, "status": "DISCOVERED", "configuration": {"proxy_password": "hunter2"}},
        )
        assert secret.status_code == 422

        assert client.get("/api/v1/newsletter-profiles/nlprofile_missing").status_code == 404


# ---------------------------------------------------------------------------
# Tenancy: cross-organization access must fail
# ---------------------------------------------------------------------------


def test_tenancy_blocks_cross_organization_profile_and_job() -> None:
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        _org(db, "org_b", "B")
        group_a = _newsletter_group(db, "org_a")
        newsletter_a = _newsletter(db, "org_a", group_a)
        profile = create_newsletter_profile(db, "org_a", "admin_a", newsletter_a.id, "TEST_PASSED", 0.9, {"email_selector": "input"})

        # Organization B cannot see or act on A's profile.
        assert get_newsletter_profile_by_id(db, "org_b", profile["id"]) is None
        try:
            promote_newsletter_profile(db, "org_b", "admin_b", newsletter_a.id, 1)
            assert False, "org B must not promote org A's profile"
        except NewsletterValidationError:
            pass

        # Organization B cannot create a job targeting A's newsletter or groups.
        agroup_b = _account_group(db, "org_b")
        _account(db, "org_b", agroup_b, "b@example.com")
        try:
            create_newsletter_job(db, "org_b", "admin_b", newsletter_a.id, None, agroup_b.id, 4, {}, False)
            assert False, "org B must not target org A's newsletter"
        except NewsletterValidationError:
            pass
        try:
            create_newsletter_job(db, "org_b", "admin_b", None, group_a.id, agroup_b.id, 4, {}, False)
            assert False, "org B must not target org A's newsletter group"
        except NewsletterValidationError:
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Subscriptions: idempotency gate + cross-org safety + no secrets
# ---------------------------------------------------------------------------


def test_subscription_unique_constraint_is_idempotency_gate() -> None:
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        group = _account_group(db, "org_a")
        account = _account(db, "org_a", group, "sub@example.com")
        newsletter = _newsletter(db, "org_a", _newsletter_group(db, "org_a"))
        first = NewsletterSubscription(
            id=f"subs_a_1", organization_id="org_a", account_id=account.id, newsletter_id=newsletter.id, state="PENDING"
        )
        db.add(first)
        db.commit()
        duplicate = NewsletterSubscription(
            id=f"subs_a_2", organization_id="org_a", account_id=account.id, newsletter_id=newsletter.id, state="SUBMITTED"
        )
        db.add(duplicate)
        try:
            db.commit()
            assert False, "second subscription for the same (organization, account, newsletter) must fail"
        except IntegrityError:
            db.rollback()
    finally:
        db.close()


def test_standalone_signup_manual_review_closes_session_and_unlocks_profile(monkeypatch) -> None:
    from app.service import _report_observer_event

    db: Session = SessionLocal()
    try:
        org = _org(db, "org_signup_review", "Signup review")
        group = _account_group(db, org.id)
        account = _account(db, org.id, group, "review@example.com")
        newsletter = _newsletter(db, org.id, _newsletter_group(db, org.id))
        profile = BrowserProfile(
            id="browser_profile_signup_review",
            organization_id=org.id,
            account_id=account.id,
            storage_reference="profiles/signup-review",
            status="OPEN",
            lock_state="LOCKED",
            active_session_id="session_signup_review",
        )
        subscription = NewsletterSubscription(
            id="subscription_signup_review",
            organization_id=org.id,
            account_id=account.id,
            newsletter_id=newsletter.id,
            state="IN_PROGRESS",
        )
        session = BrowserSession(
            id="session_signup_review",
            organization_id=org.id,
            account_id=account.id,
            profile_id=profile.id,
            state="RUNNING",
            login_state="AUTHENTICATED",
            runtime_type="LOCAL_CHROME",
            metadata_json={
                "purpose": "newsletter",
                "newsletter_subscription_id": subscription.id,
                "newsletter_id": newsletter.id,
                "browser_opened": True,
            },
        )
        db.add_all([profile, subscription, session])
        db.commit()
        monkeypatch.setattr("app.service.publish", lambda *_args, **_kwargs: None)

        _report_observer_event(session.id, "newsletter.signup.manual_review", "No completion result", result="attention")

        db.expire_all()
        assert db.get(BrowserSession, session.id).state == "CLOSED"
        assert db.get(BrowserSession, session.id).login_state == "NEWSLETTER_MANUAL_REVIEW"
        assert db.get(BrowserProfile, profile.id).lock_state == "UNLOCKED"
        assert db.get(NewsletterSubscription, subscription.id).state == "MANUAL_REVIEW"
    finally:
        db.close()


def test_skill_learning_error_does_not_leave_bulk_task_running(monkeypatch) -> None:
    from app.service import _report_observer_event

    db: Session = SessionLocal()
    try:
        org = _org(db, "org_skill_write_error", "Skill write error")
        group = _account_group(db, org.id)
        account = _account(db, org.id, group, "skill-error@example.com")
        newsletter = _newsletter(db, org.id, _newsletter_group(db, org.id))
        subscription = NewsletterSubscription(
            id="subscription_skill_write_error",
            organization_id=org.id,
            account_id=account.id,
            newsletter_id=newsletter.id,
            state="IN_PROGRESS",
        )
        db.add(subscription)
        db.commit()
        job_payload = create_newsletter_job(db, org.id, "admin", newsletter.id, None, group.id, 1, {}, False)
        materialize_newsletter_job(db, org.id, "admin", job_payload["id"])
        job = db.get(NewsletterJob, job_payload["id"])
        task = db.scalar(select(NewsletterTask).where(NewsletterTask.job_id == job.id))
        assert job is not None and task is not None
        profile = BrowserProfile(
            id="browser_profile_skill_write_error",
            organization_id=org.id,
            account_id=account.id,
            storage_reference="profiles/skill-write-error",
            status="OPEN",
            lock_state="LOCKED",
            active_session_id="session_skill_write_error",
        )
        session = BrowserSession(
            id="session_skill_write_error",
            organization_id=org.id,
            account_id=account.id,
            profile_id=profile.id,
            state="RUNNING",
            login_state="AUTHENTICATED",
            runtime_type="LOCAL_CHROME",
            metadata_json={
                "purpose": "newsletter",
                "newsletter_job_id": job.id,
                "newsletter_task_id": task.id,
                "newsletter_subscription_id": subscription.id,
                "newsletter_id": newsletter.id,
                "browser_opened": True,
            },
        )
        job.status = "RUNNING"
        task.state = "PROCESSING"
        task.browser_session_id = session.id
        db.add_all([profile, session])
        db.commit()

        def fail_skill_write(*_args, **_kwargs):
            raise RuntimeError("simulated skill persistence failure")

        monkeypatch.setattr("app.newsletter_service.record_newsletter_skill", fail_skill_write)
        monkeypatch.setattr("app.service.publish", lambda *_args, **_kwargs: None)
        _report_observer_event(
            session.id,
            "newsletter.signup.submitted",
            "Signup completed",
            event_metadata={"newsletter_skill": {"actions": [{"kind": "click", "target": "submit_button"}]}},
        )

        db.expire_all()
        assert db.get(NewsletterTask, task.id).state == "SUBMITTED"
        assert db.get(NewsletterJob, job.id).status == "COMPLETED"
    finally:
        db.close()


def test_subscription_cross_organization_safety() -> None:
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        _org(db, "org_b", "B")
        group = _account_group(db, "org_a")
        account = _account(db, "org_a", group, "cross@example.com")
        newsletter = _newsletter(db, "org_a", _newsletter_group(db, "org_a"))
        db.add(NewsletterSubscription(id="subs_a_1", organization_id="org_a", account_id=account.id, newsletter_id=newsletter.id, state="CONFIRMED"))
        db.commit()

        items_a, total_a = list_newsletter_subscriptions(db, "org_a")
        assert total_a == 1 and items_a[0]["state"] == "CONFIRMED"
        items_b, total_b = list_newsletter_subscriptions(db, "org_b")
        assert total_b == 0 and items_b == []
        assert get_newsletter_subscription(db, "org_b", "subs_a_1") is None

        filtered = list_newsletter_subscriptions(db, "org_a", state="PENDING")
        assert filtered[1] == 0
    finally:
        db.close()


def test_subscription_response_contains_no_gmail_secret_data() -> None:
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        group = _account_group(db, "org_a")
        account = _account(db, "org_a", group, "paladin@example.com")
        newsletter = _newsletter(db, "org_a", _newsletter_group(db, "org_a"))
        db.add(
            NewsletterSubscription(
                id="subs_a_1", organization_id="org_a", account_id=account.id, newsletter_id=newsletter.id, state="PENDING", last_error="slow page"
            )
        )
        db.commit()
        row = get_newsletter_subscription(db, "org_a", "subs_a_1")
        serialized = str(row)
        for forbidden in ("password", "recovery", "2fa", "totp", "otp", "cookie", "token", "secret", "hunter2"):
            assert forbidden not in serialized
        assert row["account_email"] == "paladin@example.com"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Jobs: meaningful scope, authorization, and NO execution
# ---------------------------------------------------------------------------


def test_job_definition_create_list_get_no_execution() -> None:
    db: Session = SessionLocal()
    try:
        _org(db, "org_a", "A")
        group = _newsletter_group(db, "org_a")
        newsletter = _newsletter(db, "org_a", group)
        agroup = _account_group(db, "org_a")
        _account(db, "org_a", agroup, "worker@example.com")

        job = create_newsletter_job(db, "org_a", "admin_a", newsletter.id, None, agroup.id, 4, {"max_attempts": 2}, True)
        assert job["status"] == "DRAFT"
        assert job["auto_confirm"] is True
        assert job["total_tasks"] == 0
        assert job["tasks"] == []
        assert job["started_at"] is None and job["completed_at"] is None
        assert job["newsletter_name"] == "Daily Digest"
        assert job["account_group_name"] == "Account Pool"

        # No execution occurs: no task rows exist for the job.
        assert list(job["task_counts"].values()) == []
        from app.models import NewsletterTask

        assert select_count(NewsletterTask, job["id"]) == 0

        items, total = list_newsletter_jobs(db, "org_a")
        assert total == 1 and items[0]["id"] == job["id"]
        detail = get_newsletter_job(db, "org_a", job["id"])
        assert detail is not None and detail["total_tasks"] == 0

        group_job = create_newsletter_job(db, "org_a", "admin_a", None, group.id, agroup.id, 2, {}, False)
        assert group_job["newsletter_group_name"] == group.name

        # Scope must be meaningful.
        too_many = False
        try:
            create_newsletter_job(db, "org_a", "admin_a", None, None, agroup.id, 4, {}, False)
            assert False, "job with neither newsletter nor newsletter-group target must be rejected"
        except NewsletterValidationError:
            pass
        # Account group must belong to organization.
        try:
            create_newsletter_job(db, "org_a", "admin_a", newsletter.id, None, "agroup_other_org", 4, {}, False)
            assert False, "job for an account group outside the organization must be rejected"
        except NewsletterValidationError:
            pass
        assert not too_many
    finally:
        db.close()


def test_random_newsletter_selection_is_per_account_and_stable() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_random", "Random selection")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        newsletters = [_newsletter(db, org.id, newsletter_group, f"Digest {index}") for index in range(4)]
        for newsletter in newsletters:
            _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        first = _account(db, org.id, account_group, "first@example.com")
        second = _account(db, org.id, account_group, "second@example.com")
        job = create_newsletter_job(db, org.id, "admin_random", None, newsletter_group.id, account_group.id, 2, {"randomize_newsletters": True, "random_newsletter_count": 2}, False)
        assert job["randomize_newsletters"] is True
        assert job["random_newsletter_count"] == 2

        materialized = materialize_newsletter_job(db, org.id, "admin_random", job["id"])
        assert len(materialized["tasks"]) == 4
        by_account = {account_id: [task["newsletter_id"] for task in materialized["tasks"] if task["account_id"] == account_id] for account_id in (first.id, second.id)}
        assert all(len(newsletter_ids) == 2 for newsletter_ids in by_account.values())
        assert all(len(set(newsletter_ids)) == 2 for newsletter_ids in by_account.values())

        again = materialize_newsletter_job(db, org.id, "admin_random", job["id"])
        assert len(again["tasks"]) == 4
        assert {task["id"] for task in again["tasks"]} == {task["id"] for task in materialized["tasks"]}
    finally:
        db.close()


def test_selected_newsletter_groups_share_one_per_account_random_pool() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_random_groups", "Random groups")
        first_group = _newsletter_group(db, org.id, "First")
        second_group = _newsletter_group(db, org.id, "Second")
        for index in range(2):
            first = _newsletter(db, org.id, first_group, f"First Digest {index}")
            second = _newsletter(db, org.id, second_group, f"Second Digest {index}")
            _profile(db, org.id, first, 1, "ACTIVE")
            _profile(db, org.id, second, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "multi-group@example.com")

        job = create_newsletter_job(
            db,
            org.id,
            "admin_random_groups",
            None,
            first_group.id,
            account_group.id,
            1,
            {"newsletter_group_ids": [first_group.id, second_group.id], "randomize_newsletters": True, "random_newsletter_count": 3},
            False,
        )
        materialized = materialize_newsletter_job(db, org.id, "admin_random_groups", job["id"])

        assert len(materialized["tasks"]) == 3
        assert len({task["newsletter_id"] for task in materialized["tasks"]}) == 3
        assert materialized["retry_policy_json"]["newsletter_group_ids"] == [first_group.id, second_group.id]
    finally:
        db.close()


def test_newsletter_job_can_limit_to_selected_authenticated_accounts() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_authenticated_targets", "Authenticated targets")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        newsletter = _newsletter(db, org.id, newsletter_group, "Authenticated Digest")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        authenticated = _account(db, org.id, account_group, "authenticated@example.com")
        authenticated.login_status = "AUTHENTICATED"
        authenticated.profile_status = "SAVED"
        unauthenticated = _account(db, org.id, account_group, "unauthenticated@example.com")
        unauthenticated.login_status = "NOT_STARTED"
        unauthenticated.profile_status = "RESERVED"
        db.commit()

        job = create_newsletter_job(
            db,
            org.id,
            "admin_authenticated_targets",
            newsletter.id,
            None,
            account_group.id,
            1,
            {"account_ids": [authenticated.id, unauthenticated.id], "authenticated_only": True},
            False,
        )
        materialized = materialize_newsletter_job(db, org.id, "admin_authenticated_targets", job["id"])

        assert [task["account_id"] for task in materialized["tasks"]] == [authenticated.id]
        assert materialized["retry_policy_json"]["authenticated_only"] is True
        assert materialized["retry_policy_json"]["account_ids"] == [authenticated.id, unauthenticated.id]

        empty_job = create_newsletter_job(
            db,
            org.id,
            "admin_authenticated_targets",
            newsletter.id,
            None,
            account_group.id,
            1,
            {"account_ids": [unauthenticated.id], "authenticated_only": True},
            False,
        )
        try:
            materialize_newsletter_job(db, org.id, "admin_authenticated_targets", empty_job["id"])
            assert False, "a job with no authenticated target should fail clearly"
        except NewsletterValidationError as exc:
            assert "No authenticated accounts" in str(exc)
    finally:
        db.close()


def test_newsletter_post_close_dispatch_does_not_require_worker_polling() -> None:
    db: Session = SessionLocal()
    try:
        job = SimpleNamespace(id="job-after-browser-close", organization_id="org_after_close", status="RUNNING")
        with patch("app.service.get_settings", return_value=SimpleNamespace(worker_enabled=False)), patch(
            "app.newsletter_service.dispatch_newsletter_job", return_value={"status": "RUNNING"}
        ) as dispatch:
            _maybe_dispatch_newsletter_next(db, job)
        dispatch.assert_called_once_with(db, job.organization_id, "orchestrator-api", job.id)
    finally:
        db.close()


def test_external_recipient_job_uses_ephemeral_shadow_accounts_without_secrets() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_external", "External recipients")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        newsletter = _newsletter(db, org.id, newsletter_group, "External Digest")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Permission boundary")

        job = create_newsletter_job(
            db,
            org.id,
            "admin_external",
            newsletter.id,
            None,
            account_group.id,
            1,
            {
                "recipient_mode": "EXTERNAL",
                "external_recipients": ["one@example.com", "two@example.net", "one@example.com"],
                "consent_confirmed": True,
            },
            True,
        )
        assert job["recipient_mode"] == "EXTERNAL"
        assert job["external_recipient_count"] == 2
        assert job["auto_confirm"] is False
        assert job["retry_policy_json"]["headless_browser"] is True

        materialized = materialize_newsletter_job(db, org.id, "admin_external", job["id"])
        assert len(materialized["tasks"]) == 2
        shadow_accounts = db.scalars(select(GmailAccount).where(GmailAccount.account_type == "EXTERNAL_RECIPIENT")).all()
        assert {account.email for account in shadow_accounts} == {"one@example.com", "two@example.net"}
        assert all(account.secret is None for account in shadow_accounts)
        assert all(account.browser_profile is not None and account.browser_profile.authenticated_at is None for account in shadow_accounts)
    finally:
        db.close()


def test_external_recipient_job_can_explicitly_use_a_visible_browser() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_external_visible", "External visible")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        newsletter = _newsletter(db, org.id, newsletter_group, "External Digest")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Permission boundary")

        job = create_newsletter_job(
            db,
            org.id,
            "admin_external",
            newsletter.id,
            None,
            account_group.id,
            1,
            {
                "recipient_mode": "EXTERNAL",
                "external_recipients": ["visible@example.com"],
                "consent_confirmed": True,
                "headless_browser": False,
            },
            False,
        )

        assert job["retry_policy_json"]["headless_browser"] is False
    finally:
        db.close()


def test_bulk_materialization_includes_test_required_newsletters_without_profile() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_bulk_all", "Bulk all records")
        newsletter_group = _newsletter_group(db, org.id, "All sources")
        newsletter = _newsletter(db, org.id, newsletter_group, "Imported Digest")
        newsletter.status = "TEST_REQUIRED"
        newsletter.discovery_status = "DISCOVERED"
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "all-records@example.com")
        db.commit()

        job = create_newsletter_job(db, org.id, "admin_bulk_all", None, newsletter_group.id, account_group.id, 1, {}, False)
        materialized = materialize_newsletter_job(db, org.id, "admin_bulk_all", job["id"])

        assert len(materialized["tasks"]) == 1
        assert materialized["tasks"][0]["account_id"] == account.id
        assert materialized["tasks"][0]["newsletter_id"] == newsletter.id
        assert materialized["tasks"][0]["state"] == "QUEUED"
    finally:
        db.close()


def test_dispatch_rehydrates_missing_redis_queue_from_durable_tasks(monkeypatch) -> None:
    from app.service import BrowserLaunchError

    db: Session = SessionLocal()
    try:
        org = _org(db, "org_queue_rehydrate", "Queue recovery")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        newsletter = _newsletter(db, org.id, newsletter_group, "Queue Recovery Digest")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        _account(db, org.id, account_group, "queue-recovery@example.com")
        job = create_newsletter_job(db, org.id, "admin_queue_rehydrate", newsletter.id, None, account_group.id, 1, {"max_attempts": 2}, False)

        materialize_newsletter_job(db, org.id, "admin_queue_rehydrate", job["id"])
        stored_job = db.get(NewsletterJob, job["id"])
        assert stored_job is not None
        stored_job.status = "RUNNING"
        db.commit()

        # Simulate the Redis mirror disappearing after the durable task was
        # created. The dispatch call must reconstruct it before popping work.
        worker_redis.get_redis().flushdb()
        monkeypatch.setattr(
            "app.service.start_newsletter_signup_test",
            lambda *args, **kwargs: (_ for _ in ()).throw(BrowserLaunchError("synthetic launch failure")),
        )

        dispatched = dispatch_newsletter_job(db, org.id, "admin_queue_rehydrate", job["id"])
        assert dispatched["task_counts"] == {"RETRY_PENDING": 1}
        task = dispatched["tasks"][0]
        assert task["attempts"] == 1
        assert task["last_error"] == "synthetic launch failure"
    finally:
        db.close()


def test_dispatch_pauses_on_existing_account_session_without_counting_signup_attempt(monkeypatch) -> None:
    from app.service import AccountSessionConflictError

    db: Session = SessionLocal()
    try:
        org = _org(db, "org_session_conflict", "Session conflict")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        newsletter = _newsletter(db, org.id, newsletter_group, "Session Conflict Digest")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "session-conflict@example.com")
        job_payload = create_newsletter_job(
            db, org.id, "admin_session_conflict", newsletter.id, None, account_group.id, 1, {}, False
        )
        materialize_newsletter_job(db, org.id, "admin_session_conflict", job_payload["id"])
        job = db.get(NewsletterJob, job_payload["id"])
        assert job is not None
        job.status = "RUNNING"
        db.commit()

        class LockSpy:
            def __init__(self) -> None:
                self.released: list[tuple[str, str]] = []
                self.force_released: list[str] = []

            def acquire(self, account_id: str, *_args) -> str:
                return "dispatch-token"

            def release(self, account_id: str, token: str) -> bool:
                self.released.append((account_id, token))
                return True

            def refresh(self, _account_id: str, _token: str, _ttl_ms: int) -> bool:
                return True

            def force_release(self, account_id: str) -> None:
                self.force_released.append(account_id)

        lock_spy = LockSpy()
        monkeypatch.setattr("app.service._locks", lambda: lock_spy)
        monkeypatch.setattr(
            "app.service.start_newsletter_signup_test",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AccountSessionConflictError("This account already has an open login session")
            ),
        )

        result = dispatch_newsletter_job(db, org.id, "admin_session_conflict", job.id)

        assert result["status"] == "PAUSED"
        assert result["task_counts"] == {"QUEUED": 1}
        task = result["tasks"][0]
        assert task["state"] == "QUEUED"
        assert task["attempts"] == 0
        assert "No signup was attempted" in task["last_error"]
        assert "resume the job" in task["last_error"]
        assert (account.id, "dispatch-token") in lock_spy.released
        assert len(lock_spy.released) == 2  # account lease and bulk-scope lease
        assert lock_spy.force_released == []
        event = db.scalar(
            select(NewsletterTaskEvent).where(
                NewsletterTaskEvent.task_id == task["id"],
                NewsletterTaskEvent.event == "dispatch_paused_session_conflict",
            )
        )
        assert event is not None and event.result == "attention"
    finally:
        db.close()


def test_dispatch_does_not_pause_while_same_account_newsletter_is_still_processing(monkeypatch) -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_same_account_newsletter_wave", "Same account newsletter wave")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        first_newsletter = _newsletter(db, org.id, newsletter_group, "First digest")
        second_newsletter = _newsletter(db, org.id, newsletter_group, "Second digest")
        _profile(db, org.id, first_newsletter, 1, "ACTIVE")
        _profile(db, org.id, second_newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "same-account-wave@example.com")
        job_payload = create_newsletter_job(
            db, org.id, "admin_same_account_wave", None, newsletter_group.id, account_group.id, 1, {}, False
        )
        materialized = materialize_newsletter_job(db, org.id, "admin_same_account_wave", job_payload["id"])
        job = db.get(NewsletterJob, job_payload["id"])
        assert job is not None
        running_task = next(task for task in materialized["tasks"] if task["newsletter_id"] == first_newsletter.id)
        task = db.get(NewsletterTask, running_task["id"])
        assert task is not None
        task.state = "PROCESSING"
        task.browser_session_id = "session_still_closing"
        task.lock_key = "active-account-lease"
        task.heartbeat_at = utcnow()
        job.status = "RUNNING"
        db.commit()

        result = dispatch_newsletter_job(db, org.id, "admin_same_account_wave", job.id)

        assert result["status"] == "RUNNING"
        assert result["task_counts"] == {"PROCESSING": 1, "QUEUED": 1}
        queued = next(row for row in result["tasks"] if row["state"] == "QUEUED")
        assert queued["account_id"] == account.id
    finally:
        db.close()


def test_dispatch_profileless_bulk_task_keeps_configured_slot_and_lease(monkeypatch) -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_profileless_dispatch", "Profileless dispatch")
        newsletter_group = _newsletter_group(db, org.id, "Imported")
        newsletter = _newsletter(db, org.id, newsletter_group, "Imported Source")
        newsletter.status = "TEST_REQUIRED"
        newsletter.discovery_status = "DISCOVERED"
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "profileless@example.com")
        profile = BrowserProfile(
            id=f"profile_{account.id}",
            organization_id=org.id,
            account_id=account.id,
            storage_reference=f"profiles/{org.id}/{account.id}",
            status="OPEN",
            lock_state="LOCKED",
        )
        session = BrowserSession(
            id="session_profileless_dispatch",
            organization_id=org.id,
            account_id=account.id,
            profile_id=profile.id,
            state="RUNNING",
            login_state="AUTHENTICATED",
            runtime_type="LOCAL_CHROME",
            metadata_json={"purpose": "newsletter"},
        )
        db.add_all([profile, session])
        db.commit()

        job = create_newsletter_job(db, org.id, "admin_profileless_dispatch", newsletter.id, None, account_group.id, 1, {"random_newsletter_count": 1}, False)
        materialize_newsletter_job(db, org.id, "admin_profileless_dispatch", job["id"])
        stored_job = db.get(NewsletterJob, job["id"])
        assert stored_job is not None
        stored_job.status = "RUNNING"
        db.commit()

        monkeypatch.setattr("app.service.start_newsletter_signup_test", lambda *args, **kwargs: session)
        dispatched = dispatch_newsletter_job(db, org.id, "admin_profileless_dispatch", job["id"])

        task = dispatched["tasks"][0]
        assert task["state"] == "PROCESSING"
        assert task["profile_version"] is None
        assert task["browser_session_id"] == session.id
        assert dispatched["concurrency_limit"] == 1
        assert dispatched["random_newsletter_count"] == 1
    finally:
        db.close()


def test_dispatch_does_not_overwrite_terminal_signup_event_arriving_during_launch(monkeypatch) -> None:
    """A fast observer result must win over the launcher's stale task row."""
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_dispatch_race", "Dispatch race")
        newsletter_group = _newsletter_group(db, org.id, "Race")
        newsletter = _newsletter(db, org.id, newsletter_group, "Race Source")
        newsletter.confirmation_required = False
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "dispatch-race@example.com")
        profile = BrowserProfile(
            id=f"profile_{account.id}",
            organization_id=org.id,
            account_id=account.id,
            storage_reference=f"profiles/{org.id}/{account.id}",
            status="OPEN",
            lock_state="LOCKED",
        )
        session = BrowserSession(
            id="session_dispatch_race",
            organization_id=org.id,
            account_id=account.id,
            profile_id=profile.id,
            state="RUNNING",
            login_state="AUTHENTICATED",
            runtime_type="LOCAL_CHROME",
            metadata_json={"purpose": "newsletter"},
        )
        db.add_all([profile, session])
        db.commit()

        job = create_newsletter_job(db, org.id, "admin_dispatch_race", newsletter.id, None, account_group.id, 1, {"max_attempts": 2}, False)
        materialize_newsletter_job(db, org.id, "admin_dispatch_race", job["id"])
        stored_job = db.get(NewsletterJob, job["id"])
        assert stored_job is not None
        stored_job.status = "RUNNING"
        db.commit()

        def fake_start(db_session, _organization_id, _actor_id, _newsletter_id, _account_id, _profile_id, **_kwargs):
            task = db_session.scalar(select(NewsletterTask).where(NewsletterTask.job_id == job["id"]))
            assert task is not None
            # This is the observer callback completing between browser launch
            # and the dispatcher's lease-binding commit.
            task.state = "SUBMITTED"
            task.browser_session_id = None
            db_session.commit()
            return session

        monkeypatch.setattr("app.service.start_newsletter_signup_test", fake_start)
        dispatched = dispatch_newsletter_job(db, org.id, "admin_dispatch_race", job["id"])

        task = db.scalar(select(NewsletterTask).where(NewsletterTask.job_id == job["id"]))
        assert task is not None
        assert task.state == "SUBMITTED"
        assert task.browser_session_id is None
        assert dispatched["status"] == "COMPLETED"
    finally:
        db.close()


def test_dispatch_shared_bulk_scope_enforces_one_global_concurrency_limit(monkeypatch) -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_shared_bulk_limit", "Shared bulk limit")
        newsletter_group = _newsletter_group(db, org.id, "Technology")
        newsletter = _newsletter(db, org.id, newsletter_group, "Shared Bulk Source")
        _profile(db, org.id, newsletter, 1, "ACTIVE")

        account_group_one = _account_group(db, org.id, "Accounts one")
        account_group_two = _account_group(db, org.id, "Accounts two")
        account_one = _account(db, org.id, account_group_one, "shared-one@example.com")
        account_two = _account(db, org.id, account_group_two, "shared-two@example.com")
        for account in (account_one, account_two):
            browser_profile = BrowserProfile(
                id=f"browser_profile_{account.id}",
                organization_id=org.id,
                account_id=account.id,
                storage_reference=f"profiles/{org.id}/{account.id}",
                status="SAVED",
                lock_state="UNLOCKED",
            )
            browser_session = BrowserSession(
                id=f"browser_session_{account.id}",
                organization_id=org.id,
                account_id=account.id,
                profile_id=browser_profile.id,
                state="RUNNING",
                login_state="AUTHENTICATED",
                runtime_type="LOCAL_CHROME",
                metadata_json={"purpose": "newsletter"},
            )
            db.add_all([browser_profile, browser_session])
        db.commit()

        retry_policy = {"bulk_run_id": "shared-run", "bulk_concurrency_limit": 1}
        first = create_newsletter_job(db, org.id, "admin_shared_bulk", newsletter.id, None, account_group_one.id, 1, retry_policy, False)
        second = create_newsletter_job(db, org.id, "admin_shared_bulk", newsletter.id, None, account_group_two.id, 1, retry_policy, False)
        for created in (first, second):
            materialize_newsletter_job(db, org.id, "admin_shared_bulk", created["id"])
            stored = db.get(NewsletterJob, created["id"])
            assert stored is not None
            stored.status = "RUNNING"
        db.commit()

        def fake_start_signup(db_session, _organization_id, _actor_id, _newsletter_id, account_id, _profile_id, **_kwargs):
            return db_session.get(BrowserSession, f"browser_session_{account_id}")

        monkeypatch.setattr("app.service.start_newsletter_signup_test", fake_start_signup)

        # A concurrent dispatcher for the same bulk scope must not start a
        # second wave while another worker owns the scope decision.
        from app.service import _locks, reconcile_queue_mirrors
        lock_id = "newsletter-dispatch-" + hashlib.sha256(org.id.encode()).hexdigest()[:32]
        dispatch_token = _locks().acquire(lock_id, "other-worker", "", 120_000)
        assert dispatch_token is not None
        try:
            blocked = dispatch_newsletter_job(db, org.id, "admin_shared_bulk", first["id"])
            assert blocked["task_counts"] == {"QUEUED": 1}
        finally:
            _locks().release(lock_id, dispatch_token)

        first_dispatch = dispatch_newsletter_job(db, org.id, "admin_shared_bulk", first["id"])
        second_dispatch = dispatch_newsletter_job(db, org.id, "admin_shared_bulk", second["id"])

        assert first_dispatch["task_counts"] == {"PROCESSING": 1}
        assert second_dispatch["task_counts"] == {"QUEUED": 1}
        from app.worker.queue import RedisQueue
        assert RedisQueue().newsletter_bulk_running_count("shared-run") == 1
        reconcile_queue_mirrors(db, org.id)
        assert RedisQueue().newsletter_bulk_running_count("shared-run") == 1
    finally:
        db.close()


def test_dispatch_respects_durable_processing_count_when_redis_mirror_is_empty() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_durable_concurrency_cap", "Durable concurrency cap")
        newsletter_group = _newsletter_group(db, org.id)
        newsletter = _newsletter(db, org.id, newsletter_group)
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id)
        accounts = [_account(db, org.id, account_group, f"cap-{index}@example.com") for index in range(2)]
        job_payload = create_newsletter_job(db, org.id, "admin", newsletter.id, None, account_group.id, 1, {}, False)
        materialize_newsletter_job(db, org.id, "admin", job_payload["id"])
        job = db.get(NewsletterJob, job_payload["id"])
        tasks = db.scalars(select(NewsletterTask).where(NewsletterTask.job_id == job.id).order_by(NewsletterTask.created_at)).all()
        job.status = "RUNNING"
        tasks[0].state = "PROCESSING"
        db.commit()

        # No Redis running marker exists here. Durable task state must still
        # prevent the dispatcher from opening a second session over the cap.
        result = dispatch_newsletter_job(db, org.id, "admin", job.id)

        assert result["task_counts"] == {"PROCESSING": 1, "QUEUED": 1}
    finally:
        db.close()


def test_dispatch_fills_every_concurrency_slot_with_a_distinct_account(monkeypatch) -> None:
    """A wave of N slots must use N accounts, not N tasks from one account.

    Materialization queues one account's whole backlog before the next account's,
    so ordering by ``created_at`` alone left the other slots empty whenever the
    first account held a lease.
    """
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_wave_interleave", "Wave interleave")
        newsletter_group = _newsletter_group(db, org.id, "Imported")
        account_group = _account_group(db, org.id, "Accounts")
        accounts = [_account(db, org.id, account_group, f"wave-{index}@example.com") for index in range(10)]
        newsletters = [_newsletter(db, org.id, newsletter_group, f"Imported Source {index}") for index in range(3)]
        for newsletter in newsletters:
            _profile(db, org.id, newsletter, 1, "ACTIVE")
        for account in accounts:
            db.add(
                BrowserProfile(
                    id=f"browser_profile_{account.id}",
                    organization_id=org.id,
                    account_id=account.id,
                    storage_reference=f"profiles/{org.id}/{account.id}",
                    status="SAVED",
                    lock_state="UNLOCKED",
                )
            )
        db.commit()

        job_payload = create_newsletter_job(
            db, org.id, "admin_wave_interleave", None, newsletter_group.id, account_group.id, 10, {}, False
        )
        materialize_newsletter_job(db, org.id, "admin_wave_interleave", job_payload["id"])
        job = db.get(NewsletterJob, job_payload["id"])
        assert job is not None
        job.status = "RUNNING"
        db.commit()
        assert db.query(NewsletterTask).filter(NewsletterTask.job_id == job.id).count() == 30

        # The queued order itself must reach every account before any account
        # appears twice, otherwise the dispatcher cannot fill the wave.
        queued_rows = (
            db.query(NewsletterTask)
            .filter(NewsletterTask.job_id == job.id, NewsletterTask.state == "QUEUED")
            .all()
        )
        entries = _newsletter_queue_entries(queued_rows)
        head_accounts = [account_id for _, account_id, _ in entries[:10]]
        assert len(set(head_accounts)) == 10, head_accounts
        task_by_id = {task.id: task for task in queued_rows}
        # Each 10-task website round covers all selected accounts exactly once;
        # then the next round advances all accounts to the next publisher.
        for round_index in range(3):
            website_round = entries[round_index * 10:(round_index + 1) * 10]
            assert len({account_id for _, account_id, _ in website_round}) == 10
            assert len({task_by_id[task_id].newsletter_id for task_id, _, _ in website_round}) == 1
        assert entries == sorted(entries, key=lambda entry: entry[2])

        def fake_start_signup(db_session, _organization_id, _actor_id, _newsletter_id, account_id, _profile_id, **_kwargs):
            browser_profile = db_session.query(BrowserProfile).filter(BrowserProfile.account_id == account_id).first()
            sequence = db_session.query(BrowserSession).filter(BrowserSession.account_id == account_id).count() + 1
            session = BrowserSession(
                id=f"browser_session_{account_id}_{sequence}",
                organization_id=browser_profile.organization_id,
                account_id=account_id,
                profile_id=browser_profile.id,
                state="RUNNING",
                login_state="AUTHENTICATED",
                runtime_type="LOCAL_CHROME",
                metadata_json={"purpose": "newsletter"},
            )
            db_session.add(session)
            db_session.flush()
            return session

        monkeypatch.setattr("app.service.start_newsletter_signup_test", fake_start_signup)

        result = dispatch_newsletter_job(db, org.id, "admin_wave_interleave", job.id)

        # 10 accounts x 3 newsletters: exactly one task per account runs.
        assert result["task_counts"] == {"PROCESSING": 10, "QUEUED": 20}
        running = {task["account_id"] for task in result["tasks"] if task["state"] == "PROCESSING"}
        assert running == {account.id for account in accounts}
        assert len({task["browser_session_id"] for task in result["tasks"] if task["state"] == "PROCESSING"}) == 10
        from app.service import reconcile_queue_mirrors
        from app.worker.queue import RedisQueue
        queue = RedisQueue()
        queue.remove_active_profile(accounts[0].id)
        queue.remove_newsletter_bulk_running(job.id, next(task["id"] for task in result["tasks"] if task["state"] == "PROCESSING"))
        reconcile_queue_mirrors(db, org.id)
        assert queue.active_profile_count() == 10
        assert queue.newsletter_bulk_running_count(job.id) == 10

        # A separate bulk run in the same workspace cannot exceed the global
        # ten-session ceiling while the first ten accounts are still active.
        other_group = _account_group(db, org.id, "More accounts")
        extra_accounts = [_account(db, org.id, other_group, f"extra-{index}@example.com") for index in range(2)]
        for account in extra_accounts:
            db.add(BrowserProfile(
                id=f"browser_profile_{account.id}", organization_id=org.id, account_id=account.id,
                storage_reference=f"profiles/{org.id}/{account.id}", status="SAVED", lock_state="UNLOCKED",
            ))
        db.commit()
        extra_job = create_newsletter_job(db, org.id, "admin_wave_interleave", newsletters[0].id, None, other_group.id, 10, {}, False)
        materialize_newsletter_job(db, org.id, "admin_wave_interleave", extra_job["id"])
        db.get(NewsletterJob, extra_job["id"]).status = "RUNNING"
        db.commit()
        extra_result = dispatch_newsletter_job(db, org.id, "admin_wave_interleave", extra_job["id"])
        assert extra_result["task_counts"] == {"QUEUED": 2}

        # After one account's first browser ends, the next newsletter for
        # that same account may start, while its prior session stays closed.
        from app.newsletter_service import _release_newsletter_task_lease
        first_task = db.scalar(select(NewsletterTask).where(
            NewsletterTask.job_id == job.id, NewsletterTask.account_id == accounts[0].id,
            NewsletterTask.state == "PROCESSING",
        ))
        first_session = db.get(BrowserSession, first_task.browser_session_id)
        first_session.state = "CLOSED"
        _release_newsletter_task_lease(db, first_task)
        first_task.browser_session_id = None
        first_task.state = "SUBMITTED"
        db.commit()
        next_wave = dispatch_newsletter_job(db, org.id, "admin_wave_interleave", job.id)
        same_account = [task for task in next_wave["tasks"] if task["account_id"] == accounts[0].id]
        assert sum(task["state"] == "PROCESSING" for task in same_account) == 1
        assert sum(task["state"] == "SUBMITTED" for task in same_account) == 1
        assert db.get(BrowserSession, first_session.id).state == "CLOSED"
    finally:
        db.close()


def test_newsletter_resource_limit_throttles_new_sessions_without_exceeding_job_cap() -> None:
    gib = 1024 * 1024 * 1024
    assert _newsletter_resource_aware_limit(
        10, available_memory_bytes=2 * gib, cpu_count=8, load_average=1.1
    ) == 1
    assert _newsletter_resource_aware_limit(
        10, available_memory_bytes=8 * gib, cpu_count=8, load_average=2.1
    ) == 5
    assert _newsletter_resource_aware_limit(
        3, available_memory_bytes=32 * gib, cpu_count=64, load_average=1.0
    ) == 3


def test_waiting_confirmation_task_opens_confirmation_not_another_signup(monkeypatch) -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_bulk_confirmation", "Bulk confirmation")
        newsletter_group = _newsletter_group(db, org.id, "Sources")
        newsletter = _newsletter(db, org.id, newsletter_group, "Confirmation Source")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "confirm-bulk@example.com")
        db.add(BrowserProfile(
            id=f"browser_profile_{account.id}", organization_id=org.id, account_id=account.id,
            storage_reference=f"profiles/{org.id}/{account.id}", status="SAVED", lock_state="UNLOCKED",
        ))
        db.add(NewsletterSubscription(
            id="sub_bulk_confirmation", organization_id=org.id, account_id=account.id,
            newsletter_id=newsletter.id, state="SUBMITTED",
        ))
        db.commit()
        created = create_newsletter_job(db, org.id, "admin_bulk_confirmation", newsletter.id, None, account_group.id, 1, {}, True)
        materialize_newsletter_job(db, org.id, "admin_bulk_confirmation", created["id"])
        job = db.get(NewsletterJob, created["id"])
        task = db.scalar(select(NewsletterTask).where(NewsletterTask.job_id == job.id))
        assert task is not None
        task.state = "WAITING_CONFIRMATION"
        job.status = "RUNNING"
        db.commit()
        calls = []

        def fake_confirmation(db_session, _organization_id, _actor_id, _newsletter_id, account_id, _profile_id, _subscription_id, **_kwargs):
            calls.append(account_id)
            profile = db_session.scalar(select(BrowserProfile).where(BrowserProfile.account_id == account_id))
            session = BrowserSession(
                id="session_bulk_confirmation", organization_id=org.id, account_id=account_id,
                profile_id=profile.id, state="RUNNING", login_state="AUTHENTICATED",
                runtime_type="LOCAL_CHROME", metadata_json={"purpose": "newsletter_confirm"},
            )
            db_session.add(session)
            db_session.flush()
            return session

        monkeypatch.setattr("app.service.start_newsletter_confirmation_test", fake_confirmation)
        monkeypatch.setattr("app.service.start_newsletter_signup_test", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate signup")))
        dispatched = dispatch_newsletter_job(db, org.id, "admin_bulk_confirmation", job.id)

        assert calls == [account.id]
        assert dispatched["task_counts"] == {"PROCESSING": 1}
        assert db.scalar(select(NewsletterTaskEvent).where(NewsletterTaskEvent.task_id == task.id, NewsletterTaskEvent.event == "confirmation_dispatched")) is not None
    finally:
        db.close()


def test_dispatch_deferring_a_busy_account_does_not_pause_a_healthy_multi_account_job(monkeypatch) -> None:
    """One reserved account must not stop the rest of a bulk run."""
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_busy_account_deferral", "Busy account deferral")
        newsletter_group = _newsletter_group(db, org.id, "Imported")
        newsletter = _newsletter(db, org.id, newsletter_group, "Imported Source")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        accounts = [_account(db, org.id, account_group, f"defer-{index}@example.com") for index in range(3)]
        for account in accounts:
            db.add(
                BrowserProfile(
                    id=f"browser_profile_{account.id}",
                    organization_id=org.id,
                    account_id=account.id,
                    storage_reference=f"profiles/{org.id}/{account.id}",
                    status="SAVED",
                    lock_state="UNLOCKED",
                )
            )
        db.commit()

        # An account may hold only one browser profile, so the operator session
        # reuses the account's existing profile.
        busy_account = accounts[0]
        busy_profile_id = f"browser_profile_{busy_account.id}"
        db.add(
            BrowserSession(
                id=f"operator_session_{busy_account.id}",
                organization_id=org.id,
                account_id=busy_account.id,
                profile_id=busy_profile_id,
                state="RUNNING",
                login_state="AUTHENTICATED",
                runtime_type="LOCAL_CHROME",
                metadata_json={"purpose": "operator"},
            )
        )
        db.commit()

        job_payload = create_newsletter_job(
            db, org.id, "admin_busy_deferral", newsletter.id, None, account_group.id, 3, {}, False
        )
        materialize_newsletter_job(db, org.id, "admin_busy_deferral", job_payload["id"])
        job = db.get(NewsletterJob, job_payload["id"])
        assert job is not None
        job.status = "RUNNING"
        db.commit()

        def fake_start_signup(db_session, _organization_id, _actor_id, _newsletter_id, account_id, _profile_id, **_kwargs):
            if account_id == busy_account.id:
                from app.service import AccountSessionConflictError

                raise AccountSessionConflictError("This account already has an open login session")
            browser_profile = db_session.query(BrowserProfile).filter(BrowserProfile.account_id == account_id).first()
            session = BrowserSession(
                id=f"browser_session_{account_id}",
                organization_id=browser_profile.organization_id,
                account_id=account_id,
                profile_id=browser_profile.id,
                state="RUNNING",
                login_state="AUTHENTICATED",
                runtime_type="LOCAL_CHROME",
                metadata_json={"purpose": "newsletter"},
            )
            db_session.add(session)
            db_session.flush()
            return session

        monkeypatch.setattr("app.service.start_newsletter_signup_test", fake_start_signup)

        result = dispatch_newsletter_job(db, org.id, "admin_busy_deferral", job.id)

        # The busy account is parked, the other two accounts still run, and the
        # job stays RUNNING instead of being paused by one reserved account.
        assert result["status"] == "RUNNING"
        assert result["task_counts"] == {"PROCESSING": 2, "QUEUED": 1}
        paused = db.scalar(
            select(NewsletterTaskEvent).where(
                NewsletterTaskEvent.event == "dispatch_paused_session_conflict",
            )
        )
        assert paused is not None
        assert paused.result == "attention"
    finally:
        db.close()


def test_recover_stale_newsletter_task_releases_closed_session_even_with_fresh_heartbeat() -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_closed_session_recovery", "Closed session recovery")
        newsletter_group = _newsletter_group(db, org.id, "Recovery")
        newsletter = _newsletter(db, org.id, newsletter_group, "Recovery Source")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "closed-session@example.com")
        profile = BrowserProfile(
            id=f"profile_{account.id}",
            organization_id=org.id,
            account_id=account.id,
            storage_reference=f"profiles/{org.id}/{account.id}",
            status="RESERVED",
            lock_state="UNLOCKED",
        )
        session = BrowserSession(
            id="session_closed_recovery",
            organization_id=org.id,
            account_id=account.id,
            profile_id=profile.id,
            state="CLOSED",
            login_state="AUTHENTICATED",
            runtime_type="LOCAL_CHROME",
            metadata_json={"purpose": "newsletter"},
        )
        db.add_all([profile, session])
        db.commit()

        job = create_newsletter_job(db, org.id, "admin_closed_recovery", newsletter.id, None, account_group.id, 1, {}, False)
        materialize_newsletter_job(db, org.id, "admin_closed_recovery", job["id"])
        task = db.scalar(select(NewsletterTask).where(NewsletterTask.job_id == job["id"]))
        stored_job = db.get(NewsletterJob, job["id"])
        assert task is not None and stored_job is not None
        stored_job.status = "RUNNING"
        task.state = "PROCESSING"
        task.browser_session_id = session.id
        task.worker_id = "worker-test"
        task.lock_key = "lock-test"
        task.heartbeat_at = utcnow()
        db.commit()

        result = recover_stale_newsletter_tasks(db, org.id)

        db.refresh(task)
        assert result["requeued"] == 1
        assert task.state == "QUEUED"
        assert task.browser_session_id is None
    finally:
        db.close()


def test_dispatch_clears_closed_session_reference_from_queued_task(monkeypatch) -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_queued_closed_session", "Queued closed session")
        newsletter_group = _newsletter_group(db, org.id, "Recovery")
        newsletter = _newsletter(db, org.id, newsletter_group, "Recovery Source")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "queued-closed-session@example.com")
        profile = BrowserProfile(
            id=f"profile_{account.id}",
            organization_id=org.id,
            account_id=account.id,
            storage_reference=f"profiles/{org.id}/{account.id}",
            status="RESERVED",
            lock_state="UNLOCKED",
        )
        stale_session = BrowserSession(
            id="session_queued_closed_reference",
            organization_id=org.id,
            account_id=account.id,
            profile_id=profile.id,
            state="CLOSED",
            login_state="NEWSLETTER_MANUAL_REVIEW",
            runtime_type="LOCAL_CHROME",
            metadata_json={"purpose": "newsletter"},
        )
        db.add_all([profile, stale_session])
        db.commit()

        job_payload = create_newsletter_job(db, org.id, "admin_queued_closed", newsletter.id, None, account_group.id, 1, {}, False)
        materialize_newsletter_job(db, org.id, "admin_queued_closed", job_payload["id"])
        job = db.get(NewsletterJob, job_payload["id"])
        task = db.scalar(select(NewsletterTask).where(NewsletterTask.job_id == job_payload["id"]))
        assert job is not None and task is not None
        job.status = "RUNNING"
        task.browser_session_id = stale_session.id
        task.last_error = "Stale worker recovered; queued for retry"
        db.commit()

        def fake_start(*_args, **_kwargs):
            session = BrowserSession(
                id="session_queued_closed_replacement",
                organization_id=org.id,
                account_id=account.id,
                profile_id=profile.id,
                state="RUNNING",
                login_state="AUTHENTICATED",
                runtime_type="LOCAL_CHROME",
                metadata_json={"purpose": "newsletter", "newsletter_job_id": job.id},
            )
            db.add(session)
            db.flush()
            profile.active_session_id = session.id
            profile.lock_state = "LOCKED"
            return session

        monkeypatch.setattr("app.service.start_newsletter_signup_test", fake_start)
        result = dispatch_newsletter_job(db, org.id, "admin_queued_closed", job.id)

        assert result["status"] == "RUNNING"
        assert result["task_counts"] == {"PROCESSING": 1}
        assert result["tasks"][0]["browser_session_id"] == "session_queued_closed_replacement"
        assert result["tasks"][0]["last_error"] is None
    finally:
        db.close()


def test_recover_stale_newsletter_task_does_not_requeue_after_observer_terminal_commit(monkeypatch) -> None:
    db: Session = SessionLocal()
    try:
        org = _org(db, "org_recovery_race", "Recovery race")
        newsletter_group = _newsletter_group(db, org.id, "Recovery")
        newsletter = _newsletter(db, org.id, newsletter_group, "Recovery race source")
        _profile(db, org.id, newsletter, 1, "ACTIVE")
        account_group = _account_group(db, org.id, "Accounts")
        account = _account(db, org.id, account_group, "recovery-race@example.com")
        profile = BrowserProfile(
            id=f"profile_{account.id}",
            organization_id=org.id,
            account_id=account.id,
            storage_reference=f"profiles/{org.id}/{account.id}",
            status="RESERVED",
            lock_state="UNLOCKED",
        )
        session = BrowserSession(
            id="session_recovery_race",
            organization_id=org.id,
            account_id=account.id,
            profile_id=profile.id,
            state="CLOSED",
            login_state="AUTHENTICATED",
            runtime_type="LOCAL_CHROME",
            metadata_json={"purpose": "newsletter"},
        )
        db.add_all([profile, session])
        db.commit()

        job = create_newsletter_job(db, org.id, "admin_recovery_race", newsletter.id, None, account_group.id, 1, {}, False)
        materialize_newsletter_job(db, org.id, "admin_recovery_race", job["id"])
        task = db.scalar(select(NewsletterTask).where(NewsletterTask.job_id == job["id"]))
        stored_job = db.get(NewsletterJob, job["id"])
        assert task is not None and stored_job is not None
        stored_job.status = "RUNNING"
        task.state = "PROCESSING"
        task.browser_session_id = session.id
        task.worker_id = "worker-test"
        task.lock_key = "lock-test"
        task.heartbeat_at = utcnow()
        db.commit()

        original_refresh = db.refresh

        def observer_wins(instance, *args, **kwargs):
            # Simulate the observer committing SUBMITTED after the recovery
            # query selected the stale PROCESSING row.
            if instance is task:
                task.state = "SUBMITTED"
                task.browser_session_id = None
                return
            return original_refresh(instance, *args, **kwargs)

        monkeypatch.setattr(db, "refresh", observer_wins)
        result = recover_stale_newsletter_tasks(db, org.id)

        db.refresh(task)
        assert result == {"requeued": 0, "failed": 0, "released": 0}
        assert task.state == "SUBMITTED"
        assert task.attempts == 0
    finally:
        db.close()


def test_discovery_failure_is_flagged_in_registry_and_blocks_execution() -> None:
    with SessionLocal() as db:
        org = _org(db, "org_discovery_failure")
        group = _newsletter_group(db, org.id, "Discovery")
        newsletter = _newsletter(db, org.id, group, "Unavailable Digest")

        result = record_newsletter_discovery(
            db,
            org.id,
            "admin_discovery",
            newsletter.id,
            "FAILED",
            1,
            0,
            "No safe newsletter signup form candidate was found.",
        )

        assert result["status"] == "TEST_REQUIRED"
        assert result["registry_status"] == "DISCOVERY_FAILED"
        assert result["discovery_status"] == "FAILED"
        assert result["failure_reason"] == "No safe newsletter signup form candidate was found."
        assert result["failure_at"] is not None

        stored = get_newsletter(db, org.id, newsletter.id)
        assert stored is not None
        assert stored["status"] == "TEST_REQUIRED"
        assert stored["registry_status"] == "DISCOVERY_FAILED"

        # Even if an operator manually restores ACTIVE, failed discovery is
        # still a hard bulk-execution boundary until discovery succeeds.
        newsletter.status = "ACTIVE"
        db.commit()
        job = NewsletterJob(
            id="nljob_discovery_failure",
            organization_id=org.id,
            newsletter_id=newsletter.id,
            newsletter_group_id=None,
            account_group_id="unused_account_group",
            status="DRAFT",
        )
        assert _job_targets(db, job) == []


def test_discovery_outcome_requires_a_candidate_or_explicitly_flags_blocking() -> None:
    assert _newsletter_discovery_outcome({"website_reachable": True, "candidates": []}) == (
        "FAILED",
        "No safe newsletter signup form candidate was found.",
    )
    assert _newsletter_discovery_outcome(
        {"website_reachable": True, "discovery_blocked": True, "blocked_reasons": ["BLOCKED_ANTIBOT"], "candidates": []}
    ) == ("DISCOVERY_BLOCKED", "Discovery blocked: BLOCKED_ANTIBOT")


def test_discover_all_websites_processes_each_registry_record_and_persists_outcomes(monkeypatch) -> None:
    with SessionLocal() as db:
        org = db.get(Organization, settings.demo_org_id) or _org(db, settings.demo_org_id)
        group = _newsletter_group(db, org.id, "Discovery")
        records = {
            "good": _newsletter(db, org.id, group, "Good Digest"),
            "blocked": _newsletter(db, org.id, group, "Blocked Digest"),
            "empty": _newsletter(db, org.id, group, "Empty Digest"),
            "error": _newsletter(db, org.id, group, "Error Digest"),
        }
        for key, newsletter in records.items():
            newsletter.website_url = f"https://{key}.example.com"
            newsletter.signup_url = f"https://{key}.example.com/signup"
        db.commit()

    def fake_discover(website_url: str | None, signup_url: str | None, **_kwargs) -> dict:
        # ``_kwargs`` carries the caller's stealth-fetch budget. Accepting it
        # keeps this double honest about the real signature: a mismatch here is
        # swallowed by the bulk route and silently reports every newsletter as
        # undiscovered, so the call shape is what this test is guarding.
        key = (website_url or "").split("//", 1)[-1].split(".", 1)[0]
        if key == "good":
            return {"website_reachable": True, "inspected_url_count": 2, "candidates": [{"url": signup_url}]}
        if key == "blocked":
            return {"website_reachable": True, "discovery_blocked": True, "blocked_reasons": ["BLOCKED_ANTIBOT"], "candidates": []}
        if key == "error":
            raise RuntimeError("synthetic public-page failure")
        return {"website_reachable": True, "inspected_url_count": 1, "candidates": []}

    monkeypatch.setattr("app.main.discover_newsletter", fake_discover)
    with TestClient(app) as client:
        response = client.post("/api/v1/newsletters/discover-all")

    assert response.status_code == 200
    body = response.json()
    assert body["requested_count"] == 4
    assert body["completed_count"] == 4
    assert body["skipped_count"] == 0
    assert body["discovered_count"] == 1
    assert body["blocked_count"] == 1
    assert body["failed_count"] == 2
    outcomes = {item["name"]: item for item in body["items"]}
    assert outcomes["Good Digest"]["discovery_status"] == "DISCOVERED"
    assert outcomes["Blocked Digest"]["registry_status"] == "DISCOVERY_BLOCKED"
    assert outcomes["Blocked Digest"]["failure_reason"] == "Discovery blocked: BLOCKED_ANTIBOT"
    assert outcomes["Empty Digest"]["registry_status"] == "DISCOVERY_FAILED"
    assert outcomes["Empty Digest"]["failure_reason"] == "No safe newsletter signup form candidate was found."
    assert outcomes["Error Digest"]["registry_status"] == "DISCOVERY_FAILED"
    assert outcomes["Error Digest"]["failure_reason"] == "Unexpected discovery failure while inspecting the public page safely."

    with SessionLocal() as db:
        stored = {row["name"]: row for row in list_newsletters(db, settings.demo_org_id)}
        assert stored["Good Digest"]["discovery_status"] == "DISCOVERED"
        assert stored["Blocked Digest"]["status"] == "TEST_REQUIRED"
        assert stored["Empty Digest"]["status"] == "TEST_REQUIRED"
        assert stored["Error Digest"]["status"] == "TEST_REQUIRED"


def select_count(model, job_id: int) -> int:
    from sqlalchemy import func, select

    db = SessionLocal()
    try:
        return db.scalar(select(func.count(model.id)).where(model.job_id == job_id)) or 0
    finally:
        db.close()


def test_job_api_and_account_group_authorization() -> None:
    original_auth_required = settings.auth_required
    settings.auth_required = True
    try:
        with TestClient(app) as client:
            assert client.post("/api/v1/auth/setup", json={"email": "owner@example.com", "password": "OwnerPassword123!"}).status_code == 201
            groups = client.get("/api/v1/groups").json()
            group_id = _create_api_group(client)
            newsletter_id = client.post(
                "/api/v1/newsletters", json={"group_id": group_id, "name": "Bulk Digest", "signup_url": "https://bulk.example.com/signup"}
            ).json()["id"]

            operator_created = client.post(
                "/api/v1/employees", json={"full_name": "Scoped Operator", "email": "nl-op@example.com", "role": "ACCOUNT_OPERATOR"}
            ).json()
            assert client.put(f"/api/v1/employees/{operator_created['id']}/password", json={"password": "OperatorPassword123!"}).status_code == 204
            assert (
                client.put(
                    f"/api/v1/employees/{operator_created['id']}/group-access",
                    json={"assignments": [{"group_id": groups[0]["id"], "access_level": "OPERATOR"}]},
                ).status_code
                == 200
            )

            auditor_created = client.post(
                "/api/v1/employees", json={"full_name": "Read Only", "email": "nl-auditor@example.com", "role": "AUDITOR"}
            ).json()
            assert client.put(f"/api/v1/employees/{auditor_created['id']}/password", json={"password": "AuditorPassword123!"}).status_code == 204

            assert client.post("/api/v1/auth/logout").status_code == 204
            assert client.post("/api/v1/auth/login", json={"email": "nl-op@example.com", "password": "OperatorPassword123!"}).status_code == 200

            # Operator can read the registry but not mutate it.
            assert client.get("/api/v1/newsletters").status_code == 200
            assert client.post("/api/v1/newsletter-groups", json={"name": "No No"}).status_code == 403

            # Operator may create a job for an authorized account group.
            allowed = client.post(
                "/api/v1/newsletter-jobs",
                json={"newsletter_id": newsletter_id, "account_group_id": groups[0]["id"], "concurrency_limit": 2},
            )
            assert allowed.status_code == 201
            assert allowed.json()["status"] == "DRAFT"
            assert allowed.json()["created_by"] == operator_created["id"]

            # Operator cannot target an unauthorized account group.
            denied = client.post(
                "/api/v1/newsletter-jobs",
                json={"newsletter_id": newsletter_id, "account_group_id": groups[1]["id"], "concurrency_limit": 2},
            )
            assert denied.status_code == 404

            # Jobs are scoped to visible account groups.
            visible_jobs = client.get("/api/v1/newsletter-jobs").json()
            assert visible_jobs["total"] == 1
            assert all(item["account_group_id"] == groups[0]["id"] for item in visible_jobs["items"])
            deleted = client.request("DELETE", "/api/v1/newsletter-jobs", json={"job_ids": [allowed.json()["id"]]})
            assert deleted.status_code == 200
            assert deleted.json() == {"deleted_count": 1, "deleted_ids": [allowed.json()["id"]]}
            assert client.get("/api/v1/newsletter-jobs").json()["total"] == 0

            assert client.post("/api/v1/auth/logout").status_code == 204
            assert client.post("/api/v1/auth/login", json={"email": "nl-auditor@example.com", "password": "AuditorPassword123!"}).status_code == 200
            assert client.get("/api/v1/newsletter-groups").status_code == 200
            assert client.post("/api/v1/newsletter-groups", json={"name": "No No"}).status_code == 403
    finally:
        settings.auth_required = original_auth_required


# ---------------------------------------------------------------------------
# Security: no secrets in API or audit output
# ---------------------------------------------------------------------------


def test_api_and_audit_outputs_never_expose_secrets() -> None:
    with TestClient(app) as client:
        group_id = _create_api_group(client)
        newsletter = client.post(
            "/api/v1/newsletters",
            json={
                "group_id": group_id,
                "name": "Registry Digest",
                "signup_url": "https://digest.example.com/signup",
                "allowed_confirmation_domains": ["digest.example.com"],
                "sender_patterns": ["Digest <news@digest.example.com>"],
                "subject_patterns": ["Confirm"],
            },
        ).json()
        client.post(
            "/api/v1/newsletter-profiles",
            json={"newsletter_id": newsletter["id"], "status": "TEST_PASSED", "confidence": 0.91, "configuration": {"email_selector": "input#email"}},
        )
        agroup = client.get("/api/v1/groups").json()[0]["id"]
        client.post("/api/v1/newsletter-jobs", json={"newsletter_id": newsletter["id"], "account_group_id": agroup})

        forbidden = ("password", "recovery", "2fa", "totp", "otp", "cookie", "cookie_value", "hunter2", "secret", "proxy-pass")

        def public_values(value, key: str = ""):
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    if child_key == "id" or child_key.endswith("_id"):
                        continue
                    yield from public_values(child_value, child_key)
            elif isinstance(value, list):
                for child_value in value:
                    yield from public_values(child_value, key)
            else:
                yield str(value)

        for path in (
            "/api/v1/newsletter-groups",
            "/api/v1/newsletters",
            "/api/v1/newsletter-profiles",
            "/api/v1/newsletter-subscriptions",
            "/api/v1/newsletter-jobs",
            "/api/v1/audit-events",
        ):
            response = client.get(path)
            assert response.status_code == 200
            serialized = " ".join(public_values(response.json())).lower()
            for token in forbidden:
                assert token not in serialized, f"{token} leaked in {path}"


def test_repeated_profile_failures_block_bulk_until_profile_recovery() -> None:
    with SessionLocal() as db:
        org = _org(db, "org_health")
        group = _newsletter_group(db, org.id, "Health")
        newsletter = _newsletter(db, org.id, group, "Health Digest")
        active = _profile(db, org.id, newsletter, 1, "ACTIVE")

        for index in range(3):
            db.add(
                AuditEvent(
                    id=f"audit_health_{index}",
                    organization_id=org.id,
                    actor_id="system",
                    action="newsletter_profile.tested",
                    target_type="newsletter_automation_profile",
                    target_id=active.id,
                    result="failure",
                    metadata_json={"event": "newsletter.signup.failed", "detail": "submit selector not found"},
                )
            )
        db.commit()

        health = evaluate_newsletter_automation_health(db, org.id, newsletter.id)
        db.commit()
        assert health["failure_count"] == 3
        assert health["newsletter_status"] == "TEST_REQUIRED"

        for index in range(3, 5):
            db.add(
                AuditEvent(
                    id=f"audit_health_{index}",
                    organization_id=org.id,
                    actor_id="system",
                    action="newsletter_profile.tested",
                    target_type="newsletter_automation_profile",
                    target_id=active.id,
                    result="failure",
                    metadata_json={"event": "newsletter.signup.failed", "detail": "submit selector not found"},
                )
            )
        db.commit()
        health = evaluate_newsletter_automation_health(db, org.id, newsletter.id)
        db.commit()
        assert health["newsletter_status"] == "AUTOMATION_BROKEN"
        assert health["bulk_blocked"] is True

        candidate = _profile(db, org.id, newsletter, 2, "TEST_PASSED")
        promoted = promote_newsletter_profile(db, org.id, "admin_demo", newsletter.id, candidate.version)
        assert promoted["status"] == "ACTIVE"
        assert db.get(Newsletter, newsletter.id).status == "ACTIVE"
        assert newsletter_automation_health_response(db, org.id, newsletter.id)["bulk_blocked"] is False
