"""Newsletter registry and bounded automation domain (Phases 1-11).

Phase 1 implements only the persistent domain model and CRUD foundation:

* newsletter groups (list/get/create/update/disable/enable);
* newsletters (list/get/create/update/disable/enable);
* versioned automation profiles (list/get/create/promote/reject/rollback);
* subscription reads (list/get/filter) - no signup execution;
* bounded bulk jobs with Redis dispatch, profile locks, reconciliation, and
  pause/resume/cancel/retry controls.

Public discovery, Playwright signup, Gmail confirmation, and Redis dispatch
remain constrained to the explicit newsletter workflows below; Gmail API,
IMAP, and raw credential transport are not used.

Security invariants enforced here:

* every query is organization scoped; cross-organization ids behave as
  inaccessible;
* NewsletterGroup is NEVER treated as an AccountGroup authorization id;
* promotions/rollbacks are transactional (previous ACTIVE -> SUPERSEDED);
* active-profile breakage uses repeated factual outcomes before blocking bulk work;
* audit metadata and task detail stay bounded and secret-safe.
"""

from logging import getLogger
import hashlib
import math
import os
import random
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    AccountGroup,
    AuditEvent,
    BrowserProfile,
    BrowserSession,
    GmailAccount,
    JobLock,
    Newsletter,
    NewsletterAutomationProfile,
    NewsletterAutomationSkill,
    NewsletterGroup,
    NewsletterJob,
    NewsletterSubscription,
    NewsletterTask,
    NewsletterTaskEvent,
    utcnow,
)
from .newsletter_catalog import CATALOG_GROUPS, CATALOG_SKIPPED_SOURCE_COUNT, canonical_source_key, catalog_entries
from .schemas import (
    NEWSLETTER_PROFILE_REJECTABLE_STATUSES,
    NEWSLETTER_PROFILE_ROLLBACK_STATUSES,
    bound_newsletter_configuration,
)

logger = getLogger(__name__)

HEALTH_WINDOW_SIZE = 10
TEST_REQUIRED_FAILURE_THRESHOLD = 3
AUTOMATION_BROKEN_FAILURE_THRESHOLD = 5


_SKILL_CONTROL_KEYS = ("tag", "type", "id", "name", "aria_label", "title", "placeholder", "text")


def _sanitize_newsletter_skill(payload: dict | None) -> dict | None:
    """Keep learned skills replayable but permanently secret-free and bounded."""
    if not isinstance(payload, dict):
        return None
    actions: list[dict] = []
    for raw in payload.get("actions", [])[:24] if isinstance(payload.get("actions"), list) else []:
        if not isinstance(raw, dict):
            continue
        action: dict = {}
        for key in ("kind", "target", "field_key", "selector"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                action[key] = value[:300]
        control = raw.get("control")
        if isinstance(control, dict):
            safe_control = {key: str(control[key])[:160] for key in _SKILL_CONTROL_KEYS if control.get(key) is not None}
            if safe_control:
                action["control"] = safe_control
        if action.get("kind") in {"locate", "fill", "click", "check"} and action.get("target"):
            actions.append(action)
    if not actions:
        return None
    result = {
        "schema_version": 1,
        "source": str(payload.get("source") or "deterministic")[:32],
        "website_host": str(payload.get("website_host") or "")[:255],
        "actions": actions,
    }
    return result


def get_active_newsletter_skill(db: Session, organization_id: str, newsletter_id: str) -> NewsletterAutomationSkill | None:
    return db.scalar(
        select(NewsletterAutomationSkill)
        .where(
            NewsletterAutomationSkill.organization_id == organization_id,
            NewsletterAutomationSkill.newsletter_id == newsletter_id,
            NewsletterAutomationSkill.status == "ACTIVE",
        )
        .order_by(NewsletterAutomationSkill.version.desc())
    )


def record_newsletter_skill(
    db: Session,
    organization_id: str,
    newsletter_id: str,
    profile_id: str | None,
    payload: dict | None,
) -> NewsletterAutomationSkill | None:
    """Persist a new learned skill after the signup result is factual."""
    skill_json = _sanitize_newsletter_skill(payload)
    if not skill_json:
        return None
    # Concurrent accounts can learn the same newsletter at once. Serialize
    # each attempt in a savepoint and retry from the latest durable version if
    # another observer wins the unique (newsletter_id, version) insert race.
    for _attempt in range(3):
        try:
            with db.begin_nested():
                current = get_active_newsletter_skill(db, organization_id, newsletter_id)
                if current and current.skill_json == skill_json:
                    current.success_count += 1
                    current.last_success_at = utcnow()
                    current.updated_at = utcnow()
                    db.flush()
                    return current
                latest_version = int(db.scalar(
                    select(func.max(NewsletterAutomationSkill.version)).where(
                        NewsletterAutomationSkill.newsletter_id == newsletter_id
                    )
                ) or 0)
                if current:
                    current.status = "SUPERSEDED"
                    current.updated_at = utcnow()
                learned = NewsletterAutomationSkill(
                    id=_new_id("skill"),
                    organization_id=organization_id,
                    newsletter_id=newsletter_id,
                    profile_id=profile_id,
                    version=latest_version + 1,
                    status="ACTIVE",
                    skill_json=skill_json,
                    confidence=1.0,
                    validation_confidence=1.0,
                    success_count=1,
                    consecutive_failures=0,
                    last_success_at=utcnow(),
                    last_validated_at=utcnow(),
                )
                db.add(learned)
                db.flush()
                return learned
        except IntegrityError:
            # The failed insert is contained by the savepoint; reload state and
            # allocate the next version on the following attempt.
            db.expire_all()
    return None


def mark_newsletter_skill_used(db: Session, skill: NewsletterAutomationSkill) -> None:
    skill.use_count += 1
    skill.last_used_at = utcnow()
    skill.updated_at = utcnow()
    db.flush()


def mark_newsletter_skill_validated(db: Session, skill: NewsletterAutomationSkill, confidence: float) -> None:
    """Record a successful live-DOM validation without invoking a provider."""
    skill.validation_confidence = max(0.0, min(float(confidence), 1.0))
    skill.last_validated_at = utcnow()
    skill.updated_at = utcnow()
    db.flush()


def record_newsletter_skill_failure(db: Session, skill: NewsletterAutomationSkill, *, structural: bool = False) -> None:
    """Track replay failures while preserving every historical version."""
    skill.failure_count += 1
    skill.consecutive_failures += 1
    skill.last_failure_at = utcnow()
    if structural or skill.consecutive_failures >= 3:
        skill.status = "INVALID"
    elif skill.consecutive_failures >= 2:
        skill.status = "DEGRADED"
    skill.updated_at = utcnow()
    db.flush()


def _newsletter_skill_response(skill: NewsletterAutomationSkill | None, newsletter_id: str) -> dict:
    if skill is None:
        return {"newsletter_id": newsletter_id, "status": "NOT_LEARNED"}
    skill_json = skill.skill_json if isinstance(skill.skill_json, dict) else {}
    actions = skill_json.get("actions") if isinstance(skill_json.get("actions"), list) else []
    return {
        "newsletter_id": newsletter_id,
        "status": skill.status,
        "version": skill.version,
        "confidence": skill.confidence,
        "website_host": skill_json.get("website_host"),
        "action_count": len(actions),
        "use_count": skill.use_count,
        "success_count": skill.success_count,
        "failure_count": skill.failure_count,
        "consecutive_failures": skill.consecutive_failures,
        "validation_confidence": skill.validation_confidence,
        "last_used_at": skill.last_used_at,
        "last_success_at": skill.last_success_at,
        "last_failure_at": skill.last_failure_at,
        "last_validated_at": skill.last_validated_at,
    }


def newsletter_skill_response(db: Session, organization_id: str, newsletter_id: str) -> dict:
    return _newsletter_skill_response(get_active_newsletter_skill(db, organization_id, newsletter_id), newsletter_id)


def list_newsletter_skills(db: Session, organization_id: str) -> list[dict]:
    """Return active skills in one query for the newsletter control plane."""
    rows = db.scalars(
        select(NewsletterAutomationSkill)
        .where(
            NewsletterAutomationSkill.organization_id == organization_id,
            NewsletterAutomationSkill.status == "ACTIVE",
        )
        .order_by(NewsletterAutomationSkill.newsletter_id, NewsletterAutomationSkill.version.desc())
    ).all()
    seen: set[str] = set()
    result: list[dict] = []
    for skill in rows:
        if skill.newsletter_id in seen:
            continue
        seen.add(skill.newsletter_id)
        result.append(_newsletter_skill_response(skill, skill.newsletter_id))
    return result


# Curated discovery buckets. These are NewsletterGroup records, deliberately
# separate from Gmail AccountGroup permissions and safe to seed repeatedly.
NEWSLETTER_DIRECTORY_GROUP_NAMES = (
    "World News", "Breaking News", "Local News", "Politics & Public Affairs", "Geopolitics", "International Relations",
    "Business", "Entrepreneurship", "Startups", "Small Business", "Finance", "Investing", "Stock Market", "Personal Finance",
    "Economics", "Real Estate", "Ecommerce", "Marketing", "Advertising", "Sales", "Technology", "Artificial Intelligence",
    "Software Development", "SaaS", "Cybersecurity", "Digital Privacy", "Cloud Computing", "Data Science", "Web Development",
    "Mobile Technology", "Consumer Electronics", "Future Technology", "Health", "Fitness", "Nutrition", "Mental Wellness",
    "Medical Research", "Lifestyle", "Travel", "Food & Cooking", "Fashion", "Beauty", "Home & Interior", "Parenting",
    "Relationships", "Personal Development", "Entertainment", "Movies & Television", "Music", "Gaming", "Sports",
    "Football / Soccer", "Basketball", "American Football", "Tennis", "Motorsport", "Science", "Space", "Education",
    "Books & Literature", "History", "Culture", "Philosophy", "Careers", "Leadership", "Productivity", "HR & Future of Work",
    "Legal", "Architecture & Design", "Automotive", "Pets", "Dogs", "Cats", "Gardening", "Outdoor & Adventure",
    "Photography", "Luxury", "Deals & Shopping", "Sustainability", "Climate & Environment",
)


class NewsletterValidationError(ValueError):
    """Domain validation failure mapped by the API layer into an HTTP error."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


def _external_recipients(job: NewsletterJob) -> list[str]:
    policy = dict(job.retry_policy_json or {})
    if policy.get("recipient_mode") != "EXTERNAL":
        return []
    values = policy.get("external_recipients")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip().lower() for value in values if isinstance(value, str) and value.strip()))


def _ensure_external_recipient_account(db: Session, organization_id: str, group_id: str, email: str) -> GmailAccount:
    """Create a non-Gmail shadow record used only for durable newsletter tasks.

    The record lets the existing task/lock/audit machinery work without
    pretending that an external address is a connected Gmail account. It has
    no credential secret and its browser profile is intentionally ephemeral.
    """
    account = db.scalar(
        select(GmailAccount).where(
            GmailAccount.organization_id == organization_id,
            GmailAccount.account_type == "EXTERNAL_RECIPIENT",
            GmailAccount.email == email,
        )
    )
    if account:
        if account.group_id != group_id:
            account.group_id = group_id
        return account
    digest = hashlib.sha256(f"{organization_id}:{email}".encode("utf-8")).hexdigest()[:24]
    account = GmailAccount(
        id=_new_id("extacct"),
        organization_id=organization_id,
        group_id=group_id,
        email=email,
        normalized_email=f"external:{digest}",
        account_type="EXTERNAL_RECIPIENT",
        lifecycle_status="EXTERNAL_ONLY",
        readiness_status="EXTERNAL_RECIPIENT",
        login_status="NOT_APPLICABLE",
        gmail_status="NOT_CONNECTED",
        recovery_hint="Not applicable",
        two_fa_configured=False,
        proxy_status="UNASSIGNED",
        profile_id=_new_id("extprofile"),
        profile_storage_ref=f"profiles/{organization_id}/external/{digest}",
        profile_status="RESERVED",
    )
    db.add(account)
    db.add(
        BrowserProfile(
            id=account.profile_id,
            organization_id=organization_id,
            account_id=account.id,
            storage_reference=account.profile_storage_ref,
            status="RESERVED",
            lock_state="UNLOCKED",
        )
    )
    db.flush()
    return account


def _audit(
    db: Session,
    organization_id: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    result: str = "success",
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            id=_new_id("audit"),
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            metadata_json=metadata or {},
        )
    )


# ---------------------------------------------------------------------------
# Newsletter groups
# ---------------------------------------------------------------------------


def newsletter_group_response(db: Session, group: NewsletterGroup) -> dict:
    newsletter_count = db.scalar(
        select(func.count(Newsletter.id)).where(Newsletter.organization_id == group.organization_id, Newsletter.group_id == group.id)
    ) or 0
    return {
        "id": group.id,
        "organization_id": group.organization_id,
        "name": group.name,
        "description": group.description,
        "status": group.status,
        "newsletter_count": newsletter_count,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }


def _get_newsletter_group(db: Session, organization_id: str, group_id: str) -> NewsletterGroup:
    group = db.scalar(select(NewsletterGroup).where(NewsletterGroup.organization_id == organization_id, NewsletterGroup.id == group_id))
    if not group:
        raise NewsletterValidationError("Newsletter group not found")
    return group


def list_newsletter_groups(db: Session, organization_id: str) -> list[dict]:
    rows = db.scalars(select(NewsletterGroup).where(NewsletterGroup.organization_id == organization_id).order_by(NewsletterGroup.name)).all()
    return [newsletter_group_response(db, row) for row in rows]


def seed_newsletter_directory(db: Session, organization_id: str, actor_id: str) -> dict:
    """Create the curated topic directory idempotently.

    The historical lowercase ``sport`` group is treated as the canonical
    Sports bucket for seeding purposes and is left untouched so existing
    newsletter assignments are never moved or renamed implicitly.
    """
    existing = db.scalars(select(NewsletterGroup).where(NewsletterGroup.organization_id == organization_id)).all()
    by_key = {" ".join(row.name.split()).casefold(): row for row in existing}
    created_names: list[str] = []
    existing_names: list[str] = []
    aliases: list[dict[str, str]] = []
    for requested_name in NEWSLETTER_DIRECTORY_GROUP_NAMES:
        normalized = " ".join(requested_name.split())
        key = normalized.casefold()
        row = by_key.get(key)
        if row is None and key == "sports":
            row = by_key.get("sport")
            if row is not None:
                aliases.append({"existing": row.name, "canonical": normalized})
        if row is not None:
            existing_names.append(normalized)
            continue
        row = NewsletterGroup(
            id=_new_id("nlngroup"),
            organization_id=organization_id,
            name=normalized,
            description="Curated newsletter discovery topic",
            status="ACTIVE",
        )
        db.add(row)
        by_key[key] = row
        created_names.append(normalized)
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter_group.directory_seeded",
        "newsletter_group_directory",
        organization_id,
        metadata={
            "requested_count": len(NEWSLETTER_DIRECTORY_GROUP_NAMES),
            "created_count": len(created_names),
            "existing_count": len(existing_names),
            "preserved_aliases": aliases,
        },
    )
    db.commit()
    return {
        "requested_count": len(NEWSLETTER_DIRECTORY_GROUP_NAMES),
        "created_count": len(created_names),
        "existing_count": len(existing_names),
        "preserved_aliases": aliases,
        "groups": list_newsletter_groups(db, organization_id),
    }


def import_newsletter_catalog(db: Session, organization_id: str, actor_id: str) -> dict:
    """Import the supplied source list into five broad, operator-selectable groups.

    This is deliberately registry-only. Imported records are ``TEST_REQUIRED``
    and ``NOT_DISCOVERED`` so a later subscription job cannot run them before
    public discovery and a single-account validation have taken place.
    Existing groups and newsletter records are preserved and matched by their
    normalized host/path, making repeated imports safe.
    """
    group_by_key = {
        " ".join(group.name.split()).casefold(): group
        for group in db.scalars(select(NewsletterGroup).where(NewsletterGroup.organization_id == organization_id)).all()
    }
    group_key_to_id: dict[str, str] = {}
    created_groups = 0
    for key, (name, description) in {
        "news": CATALOG_GROUPS[0],
        "tech": CATALOG_GROUPS[1],
        "science": CATALOG_GROUPS[2],
        "lifestyle": CATALOG_GROUPS[3],
        "shopping": CATALOG_GROUPS[4],
    }.items():
        existing = group_by_key.get(name.casefold())
        if existing is None:
            existing = NewsletterGroup(
                id=_new_id("nlngroup"),
                organization_id=organization_id,
                name=name,
                description=description,
                status="ACTIVE",
            )
            db.add(existing)
            db.flush()
            group_by_key[name.casefold()] = existing
            created_groups += 1
        group_key_to_id[key] = existing.id

    existing_sources = {
        canonical_source_key(newsletter.website_url): newsletter
        for newsletter in db.scalars(select(Newsletter).where(Newsletter.organization_id == organization_id)).all()
        if newsletter.website_url
    }
    created_count = 0
    existing_count = 0
    for website_url, name, group_key in catalog_entries():
        source_key = canonical_source_key(website_url)
        if source_key in existing_sources:
            existing_count += 1
            continue
        newsletter = Newsletter(
            id=_new_id("nlnews"),
            organization_id=organization_id,
            group_id=group_key_to_id[group_key],
            name=name,
            website_url=website_url,
            signup_url=None,
            status="TEST_REQUIRED",
            confirmation_required=False,
            allowed_confirmation_domains=[],
            sender_patterns=[],
            subject_patterns=[],
            discovery_status="NOT_DISCOVERED",
        )
        db.add(newsletter)
        existing_sources[source_key] = newsletter
        created_count += 1

    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter.catalog_imported",
        "newsletter_catalog",
        organization_id,
        metadata={
            "group_count": len(CATALOG_GROUPS),
            "created_groups": created_groups,
            "created_newsletters": created_count,
            "existing_newsletters": existing_count,
            "skipped_invalid_sources": CATALOG_SKIPPED_SOURCE_COUNT,
        },
    )
    db.commit()
    return {
        "requested_count": len(catalog_entries()) + CATALOG_SKIPPED_SOURCE_COUNT,
        "created_count": created_count,
        "existing_count": existing_count,
        "skipped_count": CATALOG_SKIPPED_SOURCE_COUNT,
        "created_groups": created_groups,
        "groups": list_newsletter_groups(db, organization_id),
    }


def get_newsletter_group(db: Session, organization_id: str, group_id: str) -> dict | None:
    group = db.scalar(select(NewsletterGroup).where(NewsletterGroup.organization_id == organization_id, NewsletterGroup.id == group_id))
    return newsletter_group_response(db, group) if group else None


def create_newsletter_group(db: Session, organization_id: str, actor_id: str, name: str, description: str | None) -> dict:
    group = NewsletterGroup(id=_new_id("nlngroup"), organization_id=organization_id, name=name, description=description, status="ACTIVE")
    db.add(group)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise NewsletterValidationError("A newsletter group with this name already exists") from exc
    _audit(db, organization_id, actor_id, "newsletter_group.created", "newsletter_group", group.id, metadata={"name": group.name})
    db.commit()
    return newsletter_group_response(db, group)


def update_newsletter_group(
    db: Session,
    organization_id: str,
    actor_id: str,
    group_id: str,
    name: str | None,
    description: str | None,
    group_status: str | None,
) -> dict:
    group = _get_newsletter_group(db, organization_id, group_id)
    changes: dict[str, object] = {}
    if name is not None and name != group.name:
        duplicate = db.scalar(
            select(NewsletterGroup.id).where(
                NewsletterGroup.organization_id == organization_id, NewsletterGroup.name == name, NewsletterGroup.id != group.id
            )
        )
        if duplicate:
            raise NewsletterValidationError("A newsletter group with this name already exists")
        group.name = name
        changes["name"] = name
    if description != group.description:
        group.description = description
        changes["description_changed"] = True
    if group_status is not None and group_status != group.status:
        group.status = group_status
        changes["status"] = group_status
    if changes:
        _audit(db, organization_id, actor_id, "newsletter_group.updated", "newsletter_group", group.id, metadata=changes)
        db.commit()
    return newsletter_group_response(db, group)


def disable_newsletter_group(db: Session, organization_id: str, actor_id: str, group_id: str) -> dict:
    group = _get_newsletter_group(db, organization_id, group_id)
    if group.status != "DISABLED":
        group.status = "DISABLED"
        _audit(db, organization_id, actor_id, "newsletter_group.disabled", "newsletter_group", group.id, metadata={"status": "DISABLED"})
        db.commit()
    return newsletter_group_response(db, group)


def enable_newsletter_group(db: Session, organization_id: str, actor_id: str, group_id: str) -> dict:
    group = _get_newsletter_group(db, organization_id, group_id)
    if group.status != "ACTIVE":
        group.status = "ACTIVE"
        _audit(db, organization_id, actor_id, "newsletter_group.enabled", "newsletter_group", group.id, metadata={"status": "ACTIVE"})
        db.commit()
    return newsletter_group_response(db, group)


# ---------------------------------------------------------------------------
# Newsletters
# ---------------------------------------------------------------------------


def _newsletter_registry_failure(db: Session, newsletter: Newsletter) -> tuple[str | None, object | None]:
    """Return the current bounded operator-facing failure reason."""
    if newsletter.discovery_status in {"FAILED", "DISCOVERY_BLOCKED"}:
        actions = ("newsletter.discovery.failed", "newsletter.discovery.blocked")
        fallback = (
            "Discovery was blocked before a safe signup form could be approved."
            if newsletter.discovery_status == "DISCOVERY_BLOCKED"
            else "The configured public page could not be discovered safely."
        )
    elif newsletter.status in {"TEST_REQUIRED", "AUTOMATION_BROKEN"}:
        actions = ("newsletter.automation_health_changed",)
        fallback = "Newsletter automation requires operator validation before execution."
    else:
        return None, None

    event = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == newsletter.organization_id,
            AuditEvent.target_type == "newsletter",
            AuditEvent.target_id == newsletter.id,
            AuditEvent.action.in_(actions),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    metadata = event.metadata_json if event else {}
    reason = str((metadata or {}).get("reason") or (metadata or {}).get("detail") or "").strip()
    return (reason[:300] or fallback), (event.created_at if event else None)


def _newsletter_response_payload(
    newsletter: Newsletter,
    *,
    group: NewsletterGroup | None,
    profile_versions: int,
    active_version: int | None,
    failure_reason: str | None,
    failure_at: object | None,
    subscription_counts: dict[str, int] | None = None,
) -> dict:
    counts = subscription_counts or {}
    confirmed = int(counts.get("CONFIRMED", 0))
    submitted = int(counts.get("SUBMITTED", 0)) + int(counts.get("WAITING_CONFIRMATION", 0))
    review = int(counts.get("MANUAL_REVIEW", 0)) + int(counts.get("FAILED", 0))
    scored = confirmed + submitted + review
    # A confirmed subscription is stronger evidence than a submitted form.
    # This is an observed-outcome score across unique account/site pairs, not
    # the automation profile's discovery confidence.
    submitted_weight = 0.5 if newsletter.confirmation_required else 1.0
    score = round((confirmed + submitted * submitted_weight) * 100 / scored) if scored else None
    if newsletter.status == "DISABLED":
        registry_status = "DISABLED"
    elif newsletter.discovery_status == "DISCOVERY_BLOCKED":
        registry_status = "DISCOVERY_BLOCKED"
    elif newsletter.discovery_status == "FAILED":
        registry_status = "DISCOVERY_FAILED"
    else:
        registry_status = newsletter.status
    return {
        "id": newsletter.id,
        "organization_id": newsletter.organization_id,
        "group_id": newsletter.group_id,
        "group_name": group.name if group else None,
        "name": newsletter.name,
        "website_url": newsletter.website_url,
        "signup_url": newsletter.signup_url,
        "status": newsletter.status,
        "registry_status": registry_status,
        "failure_reason": failure_reason,
        "failure_at": failure_at,
        "confirmation_required": newsletter.confirmation_required,
        "allowed_confirmation_domains": list(newsletter.allowed_confirmation_domains or []),
        "sender_patterns": list(newsletter.sender_patterns or []),
        "subject_patterns": list(newsletter.subject_patterns or []),
        "discovery_status": newsletter.discovery_status,
        "last_discovered_at": newsletter.last_discovered_at,
        "last_verified_at": newsletter.last_verified_at,
        "profile_versions": profile_versions,
        "active_profile_version": active_version,
        "signup_score": score,
        "subscribed_count": confirmed + submitted,
        "confirmed_count": confirmed,
        "review_count": review,
        "scored_count": scored,
        "created_at": newsletter.created_at,
        "updated_at": newsletter.updated_at,
    }


def newsletter_response(db: Session, newsletter: Newsletter) -> dict:
    group = db.get(NewsletterGroup, newsletter.group_id) if newsletter.group_id else None
    profile_versions = db.scalar(
        select(func.count(NewsletterAutomationProfile.id)).where(
            NewsletterAutomationProfile.organization_id == newsletter.organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter.id,
        )
    ) or 0
    active_version = db.scalar(
        select(NewsletterAutomationProfile.version)
        .where(
            NewsletterAutomationProfile.organization_id == newsletter.organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter.id,
            NewsletterAutomationProfile.status == "ACTIVE",
        )
        .order_by(NewsletterAutomationProfile.version.desc())
        .limit(1)
    )
    failure_reason, failure_at = _newsletter_registry_failure(db, newsletter)
    subscription_counts = dict(db.execute(
        select(NewsletterSubscription.state, func.count(NewsletterSubscription.id))
        .where(NewsletterSubscription.organization_id == newsletter.organization_id, NewsletterSubscription.newsletter_id == newsletter.id)
        .group_by(NewsletterSubscription.state)
    ).all())
    return _newsletter_response_payload(
        newsletter,
        group=group,
        profile_versions=int(profile_versions),
        active_version=active_version,
        failure_reason=failure_reason,
        failure_at=failure_at,
        subscription_counts=subscription_counts,
    )


def _get_newsletter(db: Session, organization_id: str, newsletter_id: str) -> Newsletter:
    newsletter = db.scalar(select(Newsletter).where(Newsletter.organization_id == organization_id, Newsletter.id == newsletter_id))
    if not newsletter:
        raise NewsletterValidationError("Newsletter not found")
    return newsletter


def list_newsletters(db: Session, organization_id: str, group_id: str | None = None) -> list[dict]:
    query = select(Newsletter).where(Newsletter.organization_id == organization_id)
    if group_id:
        query = query.where(Newsletter.group_id == group_id)
    rows = db.scalars(query.order_by(Newsletter.name)).all()
    if not rows:
        return []

    newsletter_ids = [row.id for row in rows]
    group_ids = {row.group_id for row in rows if row.group_id}
    groups = {
        group.id: group
        for group in db.scalars(select(NewsletterGroup).where(NewsletterGroup.organization_id == organization_id, NewsletterGroup.id.in_(group_ids))).all()
    } if group_ids else {}

    profile_counts = {
        newsletter_id: int(count)
        for newsletter_id, count in db.execute(
            select(NewsletterAutomationProfile.newsletter_id, func.count(NewsletterAutomationProfile.id))
            .where(
                NewsletterAutomationProfile.organization_id == organization_id,
                NewsletterAutomationProfile.newsletter_id.in_(newsletter_ids),
            )
            .group_by(NewsletterAutomationProfile.newsletter_id)
        ).all()
    }
    active_versions: dict[str, int] = {}
    for newsletter_id, version in db.execute(
        select(NewsletterAutomationProfile.newsletter_id, NewsletterAutomationProfile.version)
        .where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id.in_(newsletter_ids),
            NewsletterAutomationProfile.status == "ACTIVE",
        )
        .order_by(NewsletterAutomationProfile.newsletter_id, NewsletterAutomationProfile.version.desc())
    ).all():
        active_versions.setdefault(newsletter_id, int(version))

    subscriptions_by_newsletter: dict[str, dict[str, int]] = {}
    for newsletter_id, state, count in db.execute(
        select(NewsletterSubscription.newsletter_id, NewsletterSubscription.state, func.count(NewsletterSubscription.id))
        .where(NewsletterSubscription.organization_id == organization_id, NewsletterSubscription.newsletter_id.in_(newsletter_ids))
        .group_by(NewsletterSubscription.newsletter_id, NewsletterSubscription.state)
    ).all():
        subscriptions_by_newsletter.setdefault(newsletter_id, {})[state] = int(count)

    relevant_actions = ("newsletter.discovery.failed", "newsletter.discovery.blocked", "newsletter.automation_health_changed")
    latest_failure_events: dict[str, AuditEvent] = {}
    for event in db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == organization_id,
            AuditEvent.target_type == "newsletter",
            AuditEvent.target_id.in_(newsletter_ids),
            AuditEvent.action.in_(relevant_actions),
        )
        .order_by(AuditEvent.created_at.desc())
    ).all():
        latest_failure_events.setdefault(event.target_id, event)

    result: list[dict] = []
    for newsletter in rows:
        failure_reason: str | None = None
        failure_at: object | None = None
        if newsletter.discovery_status in {"FAILED", "DISCOVERY_BLOCKED"} or newsletter.status in {"TEST_REQUIRED", "AUTOMATION_BROKEN"}:
            event = latest_failure_events.get(newsletter.id)
            fallback = (
                "Discovery was blocked before a safe signup form could be approved."
                if newsletter.discovery_status == "DISCOVERY_BLOCKED"
                else "The configured public page could not be discovered safely."
                if newsletter.discovery_status == "FAILED"
                else "Newsletter automation requires operator validation before execution."
            )
            metadata = event.metadata_json if event else {}
            failure_reason = str((metadata or {}).get("reason") or (metadata or {}).get("detail") or "").strip()[:300] or fallback
            failure_at = event.created_at if event else None
        result.append(
            _newsletter_response_payload(
                newsletter,
                group=groups.get(newsletter.group_id),
                profile_versions=profile_counts.get(newsletter.id, 0),
                active_version=active_versions.get(newsletter.id),
                failure_reason=failure_reason,
                failure_at=failure_at,
                subscription_counts=subscriptions_by_newsletter.get(newsletter.id),
            )
        )
    return result


def get_newsletter(db: Session, organization_id: str, newsletter_id: str) -> dict | None:
    newsletter = db.scalar(select(Newsletter).where(Newsletter.organization_id == organization_id, Newsletter.id == newsletter_id))
    return newsletter_response(db, newsletter) if newsletter else None


def record_newsletter_discovery(
    db: Session,
    organization_id: str,
    actor_id: str,
    newsletter_id: str,
    discovery_status: str,
    inspected_url_count: int,
    candidate_count: int,
    failure_reason: str | None = None,
) -> dict:
    """Persist only bounded discovery metadata; never store public page HTML."""
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    newsletter.discovery_status = discovery_status
    newsletter.last_discovered_at = utcnow()
    bounded_reason = (failure_reason or "").strip()[:300] or None
    if discovery_status in {"FAILED", "DISCOVERY_BLOCKED"} and newsletter.status == "ACTIVE":
        newsletter.status = "TEST_REQUIRED"
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter.discovery.completed" if discovery_status == "DISCOVERED" else "newsletter.discovery.blocked" if discovery_status == "DISCOVERY_BLOCKED" else "newsletter.discovery.failed",
        "newsletter",
        newsletter.id,
        result="success" if discovery_status == "DISCOVERED" else "blocked" if discovery_status == "DISCOVERY_BLOCKED" else "failure",
        metadata={
            "discovery_status": discovery_status,
            "inspected_url_count": max(0, min(inspected_url_count, 8)),
            "candidate_count": max(0, min(candidate_count, 6)),
            "reason": bounded_reason,
        },
    )
    db.commit()
    return newsletter_response(db, newsletter)


def create_newsletter(
    db: Session,
    organization_id: str,
    actor_id: str,
    group_id: str,
    name: str,
    website_url: str | None,
    signup_url: str | None,
    confirmation_required: bool,
    allowed_confirmation_domains: list[str],
    sender_patterns: list[str],
    subject_patterns: list[str],
    discovery_status: str,
) -> dict:
    _get_newsletter_group(db, organization_id, group_id)
    newsletter = Newsletter(
        id=_new_id("nlnews"),
        organization_id=organization_id,
        group_id=group_id,
        name=name,
        website_url=website_url,
        signup_url=signup_url,
        status="ACTIVE",
        confirmation_required=confirmation_required,
        allowed_confirmation_domains=list(allowed_confirmation_domains),
        sender_patterns=list(sender_patterns),
        subject_patterns=list(subject_patterns),
        discovery_status=discovery_status,
    )
    db.add(newsletter)
    db.flush()
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter.created",
        "newsletter",
        newsletter.id,
        metadata={
            "name": newsletter.name,
            "group_id": newsletter.group_id,
            "confirmation_required": newsletter.confirmation_required,
            "discovery_status": newsletter.discovery_status,
            "domain_count": len(newsletter.allowed_confirmation_domains or []),
        },
    )
    db.commit()
    return newsletter_response(db, newsletter)


def update_newsletter(
    db: Session,
    organization_id: str,
    actor_id: str,
    newsletter_id: str,
    group_id: str | None,
    name: str | None,
    website_url: str | None,
    signup_url: str | None,
    newsletter_status: str | None,
    confirmation_required: bool | None,
    allowed_confirmation_domains: list[str] | None,
    sender_patterns: list[str] | None,
    subject_patterns: list[str] | None,
    discovery_status: str | None,
) -> dict:
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    unchanged = {
        "group_id": newsletter.group_id,
        "name": newsletter.name,
        "website_url": newsletter.website_url,
        "signup_url": newsletter.signup_url,
        "status": newsletter.status,
        "confirmation_required": newsletter.confirmation_required,
        "allowed_confirmation_domains": list(newsletter.allowed_confirmation_domains or []),
        "sender_patterns": list(newsletter.sender_patterns or []),
        "subject_patterns": list(newsletter.subject_patterns or []),
        "discovery_status": newsletter.discovery_status,
    }
    if group_id is not None and group_id != newsletter.group_id:
        _get_newsletter_group(db, organization_id, group_id)
        newsletter.group_id = group_id
    if name is not None and name != newsletter.name:
        newsletter.name = name
    if website_url is not None and website_url != newsletter.website_url:
        newsletter.website_url = website_url or None
    if signup_url is not None and signup_url != newsletter.signup_url:
        newsletter.signup_url = signup_url or None
    if newsletter_status is not None and newsletter_status != newsletter.status:
        newsletter.status = newsletter_status
    if confirmation_required is not None and confirmation_required != newsletter.confirmation_required:
        newsletter.confirmation_required = confirmation_required
    if allowed_confirmation_domains is not None and allowed_confirmation_domains != (newsletter.allowed_confirmation_domains or []):
        newsletter.allowed_confirmation_domains = list(allowed_confirmation_domains)
    if sender_patterns is not None and sender_patterns != (newsletter.sender_patterns or []):
        newsletter.sender_patterns = list(sender_patterns)
    if subject_patterns is not None and subject_patterns != (newsletter.subject_patterns or []):
        newsletter.subject_patterns = list(subject_patterns)
    if discovery_status is not None and discovery_status != newsletter.discovery_status:
        newsletter.discovery_status = discovery_status
    changed = (
        newsletter.group_id != unchanged["group_id"]
        or newsletter.name != unchanged["name"]
        or newsletter.website_url != unchanged["website_url"]
        or newsletter.signup_url != unchanged["signup_url"]
        or newsletter.status != unchanged["status"]
        or newsletter.confirmation_required != unchanged["confirmation_required"]
        or newsletter.allowed_confirmation_domains != unchanged["allowed_confirmation_domains"]
        or newsletter.sender_patterns != unchanged["sender_patterns"]
        or newsletter.subject_patterns != unchanged["subject_patterns"]
        or newsletter.discovery_status != unchanged["discovery_status"]
    )
    if changed:
        _audit(
            db,
            organization_id,
            actor_id,
            "newsletter.updated",
            "newsletter",
            newsletter.id,
            metadata={
                "name": newsletter.name,
                "group_id": newsletter.group_id,
                "status": newsletter.status,
                "discovery_status": newsletter.discovery_status,
                "confirmation_required": newsletter.confirmation_required,
                "domain_count": len(newsletter.allowed_confirmation_domains or []),
            },
        )
        db.commit()
    return newsletter_response(db, newsletter)


def disable_newsletter(db: Session, organization_id: str, actor_id: str, newsletter_id: str) -> dict:
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    if newsletter.status != "DISABLED":
        newsletter.status = "DISABLED"
        _audit(db, organization_id, actor_id, "newsletter.disabled", "newsletter", newsletter.id, metadata={"name": newsletter.name})
        db.commit()
    return newsletter_response(db, newsletter)


def enable_newsletter(db: Session, organization_id: str, actor_id: str, newsletter_id: str) -> dict:
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    if newsletter.status == "DISABLED":
        newsletter.status = "ACTIVE"
        _audit(db, organization_id, actor_id, "newsletter.enabled", "newsletter", newsletter.id, metadata={"name": newsletter.name})
        db.commit()
    return newsletter_response(db, newsletter)


# ---------------------------------------------------------------------------
# Automation profiles (versioned, immutable; promotion/rollback transactional)
# ---------------------------------------------------------------------------


def newsletter_profile_response(profile: NewsletterAutomationProfile) -> dict:
    return {
        "id": profile.id,
        "organization_id": profile.organization_id,
        "newsletter_id": profile.newsletter_id,
        "version": profile.version,
        "status": profile.status,
        "confidence": profile.confidence,
        "configuration_keys": sorted((profile.configuration_json or {}).keys()),
        "discovered_at": profile.discovered_at,
        "tested_at": profile.tested_at,
        "promoted_at": profile.promoted_at,
        "last_success_at": profile.last_success_at,
        "last_failure_at": profile.last_failure_at,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _profile_detail_response(profile: NewsletterAutomationProfile) -> dict:
    payload = newsletter_profile_response(profile)
    payload["configuration"] = dict(profile.configuration_json or {})
    return payload


def begin_newsletter_profile_test(db: Session, organization_id: str, actor_id: str, profile: NewsletterAutomationProfile) -> None:
    """Move a candidate into an explicit in-flight test state.

    An already-active profile remains active while it is re-tested; a failed
    or candidate version must not be eligible for bulk execution during the
    test window.
    """
    if profile.status in {"CANDIDATE", "TEST_FAILED", "TEST_PASSED"}:
        profile.status = "TESTING"
        profile.tested_at = None
        _audit(
            db,
            organization_id,
            actor_id,
            "newsletter_profile.testing",
            "newsletter_automation_profile",
            profile.id,
            metadata={"newsletter_id": profile.newsletter_id, "version": profile.version},
        )


def record_newsletter_profile_test_result(db: Session, session: BrowserSession, event: str, detail: str = "") -> None:
    """Promote or fail a profile only from verified observer outcomes."""
    metadata = session.metadata_json or {}
    profile_id = metadata.get("newsletter_profile_id")
    if not profile_id:
        return
    profile = db.get(NewsletterAutomationProfile, profile_id)
    newsletter = db.get(Newsletter, metadata.get("newsletter_id")) if metadata.get("newsletter_id") else None
    if not profile or not newsletter:
        return
    if event == "newsletter.signup.submitted":
        # A newsletter requiring confirmation is still testing until the
        # confirmation workflow produces a factual success event.
        if newsletter.confirmation_required:
            return
        next_status, result = "TEST_PASSED", "success"
    elif event == "newsletter.confirmation.confirmed":
        next_status, result = "TEST_PASSED", "success"
    elif event in {"newsletter.signup.failed", "newsletter.signup.manual_review", "newsletter.signup.blocked_antibot", "newsletter.confirmation.failed", "newsletter.confirmation.manual_review"}:
        next_status, result = "TEST_FAILED", "failure"
    else:
        return
    now = utcnow()
    if profile.status != "ACTIVE":
        profile.status = next_status
    profile.tested_at = now
    if next_status == "TEST_PASSED":
        profile.last_success_at = now
    else:
        profile.last_failure_at = now
    _audit(
        db,
        session.organization_id,
        "system",
        "newsletter_profile.tested",
        "newsletter_automation_profile",
        profile.id,
        result=result,
        metadata={
            "newsletter_id": profile.newsletter_id,
            "version": profile.version,
            "status": next_status,
            "event": event,
            "detail": detail[:300],
            "active_profile_preserved": profile.status == "ACTIVE",
        },
    )
    evaluate_newsletter_automation_health(db, session.organization_id, profile.newsletter_id)


def newsletter_automation_health_response(db: Session, organization_id: str, newsletter_id: str) -> dict:
    """Return bounded health facts derived from recent profile test outcomes.

    The event window deliberately ignores a single failure and does not infer
    breakage from network/browser noise that was never reported as a newsletter
    outcome. The response contains counts and timestamps only; no page content
    or account secrets are included.
    """
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    active_profile = db.scalar(
        select(NewsletterAutomationProfile)
        .where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter_id,
            NewsletterAutomationProfile.status == "ACTIVE",
        )
        .order_by(NewsletterAutomationProfile.version.desc())
        .limit(1)
    )
    events = []
    if active_profile:
        events = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.target_type == "newsletter_automation_profile",
                AuditEvent.target_id == active_profile.id,
                AuditEvent.action == "newsletter_profile.tested",
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(HEALTH_WINDOW_SIZE)
        ).all()
    failure_events = [event for event in events if event.result == "failure"]
    success_events = [event for event in events if event.result == "success"]
    manual_review_events = [
        event for event in events if str((event.metadata_json or {}).get("event", "")).endswith("manual_review")
    ]
    selector_failures = [
        event
        for event in failure_events
        if any(token in str((event.metadata_json or {}).get("detail", "")).lower() for token in ("selector", "field", "submit", "button"))
    ]
    health_status = newsletter.status if newsletter.status in {"TEST_REQUIRED", "AUTOMATION_BROKEN", "DISABLED"} else "HEALTHY"
    return {
        "newsletter_id": newsletter_id,
        "status": health_status,
        "newsletter_status": newsletter.status,
        "active_profile_version": active_profile.version if active_profile else None,
        "window_size": len(events),
        "success_count": len(success_events),
        "failure_count": len(failure_events),
        "manual_review_count": len(manual_review_events),
        "selector_failure_count": len(selector_failures),
        "test_required_threshold": TEST_REQUIRED_FAILURE_THRESHOLD,
        "automation_broken_threshold": AUTOMATION_BROKEN_FAILURE_THRESHOLD,
        "bulk_blocked": newsletter.status in {"TEST_REQUIRED", "AUTOMATION_BROKEN", "DISABLED"},
        "last_success_at": success_events[0].created_at if success_events else None,
        "last_failure_at": failure_events[0].created_at if failure_events else None,
        "last_manual_review_at": manual_review_events[0].created_at if manual_review_events else None,
        "last_detail": str((events[0].metadata_json or {}).get("detail", ""))[:300] if events else None,
    }


def evaluate_newsletter_automation_health(db: Session, organization_id: str, newsletter_id: str) -> dict:
    """Apply safe ACTIVE -> TEST_REQUIRED -> AUTOMATION_BROKEN transitions.

    This function only advances toward a blocked state. Recovery is explicit:
    an operator must validate and promote/rollback a profile, which then marks
    the newsletter ACTIVE. That prevents one lucky success from silently
    re-enabling a broken automation.
    """
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    health = newsletter_automation_health_response(db, organization_id, newsletter_id)
    if newsletter.status == "DISABLED":
        return health
    failure_count = int(health["failure_count"])
    desired_status = newsletter.status
    if failure_count >= AUTOMATION_BROKEN_FAILURE_THRESHOLD:
        desired_status = "AUTOMATION_BROKEN"
    elif failure_count >= TEST_REQUIRED_FAILURE_THRESHOLD and newsletter.status == "ACTIVE":
        desired_status = "TEST_REQUIRED"
    if desired_status != newsletter.status:
        previous_status = newsletter.status
        newsletter.status = desired_status
        _audit(
            db,
            organization_id,
            "system",
            "newsletter.automation_health_changed",
            "newsletter",
            newsletter.id,
            result="attention",
            metadata={
                "previous_status": previous_status,
                "status": desired_status,
                "failure_count": failure_count,
                "window_size": health["window_size"],
                "reason": "repeated_newsletter_profile_failures",
            },
        )
        health["status"] = desired_status
        health["newsletter_status"] = desired_status
        health["bulk_blocked"] = True
    return health


def _get_newsletter_profile(db: Session, organization_id: str, newsletter_id: str, version: int) -> NewsletterAutomationProfile:
    profile = db.scalar(
        select(NewsletterAutomationProfile).where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter_id,
            NewsletterAutomationProfile.version == version,
        )
    )
    if not profile:
        raise NewsletterValidationError("Automation profile not found")
    return profile


def _get_newsletter_profile_by_id(db: Session, organization_id: str, profile_id: str) -> NewsletterAutomationProfile:
    profile = db.scalar(
        select(NewsletterAutomationProfile).where(
            NewsletterAutomationProfile.organization_id == organization_id, NewsletterAutomationProfile.id == profile_id
        )
    )
    if not profile:
        raise NewsletterValidationError("Automation profile not found")
    return profile


def list_newsletter_profiles(db: Session, organization_id: str, newsletter_id: str | None = None, limit: int = 100) -> list[dict]:
    query = select(NewsletterAutomationProfile).where(NewsletterAutomationProfile.organization_id == organization_id)
    if newsletter_id is not None:
        _get_newsletter(db, organization_id, newsletter_id)
        query = query.where(NewsletterAutomationProfile.newsletter_id == newsletter_id)
    rows = db.scalars(query.order_by(NewsletterAutomationProfile.created_at.desc()).limit(limit)).all()
    return [newsletter_profile_response(row) for row in rows]


def get_newsletter_profile(db: Session, organization_id: str, newsletter_id: str, version: int) -> dict | None:
    profile = db.scalar(
        select(NewsletterAutomationProfile).where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter_id,
            NewsletterAutomationProfile.version == version,
        )
    )
    return _profile_detail_response(profile) if profile else None


def get_newsletter_profile_by_id(db: Session, organization_id: str, profile_id: str) -> dict | None:
    profile = db.scalar(
        select(NewsletterAutomationProfile).where(
            NewsletterAutomationProfile.organization_id == organization_id, NewsletterAutomationProfile.id == profile_id
        )
    )
    return _profile_detail_response(profile) if profile else None


def promote_newsletter_profile_by_id(db: Session, organization_id: str, actor_id: str, profile_id: str) -> dict:
    profile = _get_newsletter_profile_by_id(db, organization_id, profile_id)
    return promote_newsletter_profile(db, organization_id, actor_id, profile.newsletter_id, profile.version)


def reject_newsletter_profile_by_id(db: Session, organization_id: str, actor_id: str, profile_id: str) -> dict:
    profile = _get_newsletter_profile_by_id(db, organization_id, profile_id)
    return reject_newsletter_profile(db, organization_id, actor_id, profile.newsletter_id, profile.version)


def rollback_newsletter_profile_by_id(db: Session, organization_id: str, actor_id: str, profile_id: str) -> dict:
    profile = _get_newsletter_profile_by_id(db, organization_id, profile_id)
    return rollback_newsletter_profile(db, organization_id, actor_id, profile.newsletter_id, profile.version)


def update_newsletter_profile_by_id(
    db: Session,
    organization_id: str,
    actor_id: str,
    profile_id: str,
    configuration: dict[str, object],
) -> dict:
    """Save an operator's correction as a new candidate version.

    A reviewed profile is an immutable record of what was tested, so a change
    is published as the next version rather than edited in place. The caller
    then runs a signup test against it, which is the documented recovery path
    from TEST_FAILED back to ACTIVE.

    Keys are merged over the source version's configuration. An empty string
    clears a key: the observer already reads a blank line as "not configured".
    """
    source = _get_newsletter_profile_by_id(db, organization_id, profile_id)
    # The credential boundary is enforced here as well as in the request
    # schema, so the service cannot be reached with a forbidden key from any
    # other caller.
    try:
        incoming = bound_newsletter_configuration(configuration)
    except ValueError as exc:
        raise NewsletterValidationError(str(exc)) from exc
    merged = dict(source.configuration_json or {})
    merged.update(incoming)
    merged = {key: value for key, value in merged.items() if value != ""}
    changed_keys = sorted(key for key in incoming if merged.get(key) != (source.configuration_json or {}).get(key))
    created = create_newsletter_profile(
        db,
        organization_id,
        actor_id,
        source.newsletter_id,
        "CANDIDATE",
        source.confidence,
        merged,
    )
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter_profile.updated",
        "newsletter_automation_profile",
        created["id"],
        metadata={
            "newsletter_id": source.newsletter_id,
            "version": created["version"],
            "source_version": source.version,
            # Names only: configuration values are never copied into audit
            # metadata, which stays a record of what changed, not of what it is.
            "changed_keys": changed_keys,
        },
    )
    db.commit()
    return created


def create_newsletter_profile(
    db: Session,
    organization_id: str,
    actor_id: str,
    newsletter_id: str,
    status: str,
    confidence: float,
    configuration: dict[str, object],
) -> dict:
    _get_newsletter(db, organization_id, newsletter_id)
    max_version = db.scalar(
        select(func.max(NewsletterAutomationProfile.version)).where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter_id,
        )
    ) or 0
    profile = NewsletterAutomationProfile(
        id=_new_id("nlprofile"),
        organization_id=organization_id,
        newsletter_id=newsletter_id,
        version=max_version + 1,
        status=status,
        confidence=confidence,
        configuration_json=dict(configuration),
        discovered_at=utcnow() if status in {"DISCOVERED", "CANDIDATE", "TESTING"} else None,
        tested_at=utcnow() if status in {"TEST_PASSED", "TEST_FAILED"} else None,
    )
    db.add(profile)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise NewsletterValidationError("Automation profile version already exists") from exc
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter_profile.created",
        "newsletter_automation_profile",
        profile.id,
        metadata={"newsletter_id": newsletter_id, "version": profile.version, "status": profile.status, "confidence": profile.confidence},
    )
    db.commit()
    return _profile_detail_response(profile)


def promote_newsletter_profile(db: Session, organization_id: str, actor_id: str, newsletter_id: str, version: int) -> dict:
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    profile = _get_newsletter_profile(db, organization_id, newsletter_id, version)
    if profile.status != "TEST_PASSED":
        raise NewsletterValidationError(f"A {profile.status.lower()} automation profile cannot be activated")
    current_active = db.scalar(
        select(NewsletterAutomationProfile).where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter_id,
            NewsletterAutomationProfile.status == "ACTIVE",
        )
    )
    if current_active and current_active.id == profile.id:
        return _profile_detail_response(profile)
    superseded_versions: list[int] = []
    previous_query = select(NewsletterAutomationProfile).where(
        NewsletterAutomationProfile.organization_id == organization_id,
        NewsletterAutomationProfile.newsletter_id == newsletter_id,
        NewsletterAutomationProfile.id != profile.id,
    )
    if current_active:
        previous_query = previous_query.where(NewsletterAutomationProfile.id == current_active.id)
    else:
        # On the first promotion, only older validated versions are retired;
        # newer candidates remain available for deliberate promotion later.
        previous_query = previous_query.where(
            NewsletterAutomationProfile.status == "TEST_PASSED",
            NewsletterAutomationProfile.version < profile.version,
        )
    previous_profiles = db.scalars(previous_query).all()
    for previous in previous_profiles:
        previous.status = "SUPERSEDED"
        previous.updated_at = utcnow()
        superseded_versions.append(previous.version)
    profile.status = "ACTIVE"
    profile.promoted_at = utcnow()
    newsletter.status = "ACTIVE"
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter_profile.promoted",
        "newsletter_automation_profile",
        profile.id,
        metadata={"newsletter_id": newsletter_id, "version": profile.version, "superseded_versions": superseded_versions[:20]},
    )
    db.commit()
    return _profile_detail_response(profile)


def reject_newsletter_profile(db: Session, organization_id: str, actor_id: str, newsletter_id: str, version: int) -> dict:
    _get_newsletter(db, organization_id, newsletter_id)
    profile = _get_newsletter_profile(db, organization_id, newsletter_id, version)
    if profile.status not in NEWSLETTER_PROFILE_REJECTABLE_STATUSES:
        raise NewsletterValidationError(f"A {profile.status.lower()} automation profile cannot be rejected")
    profile.status = "REJECTED"
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter_profile.rejected",
        "newsletter_automation_profile",
        profile.id,
        metadata={"newsletter_id": newsletter_id, "version": profile.version},
    )
    db.commit()
    return _profile_detail_response(profile)


def rollback_newsletter_profile(db: Session, organization_id: str, actor_id: str, newsletter_id: str, version: int) -> dict:
    newsletter = _get_newsletter(db, organization_id, newsletter_id)
    profile = _get_newsletter_profile(db, organization_id, newsletter_id, version)
    if profile.status not in NEWSLETTER_PROFILE_ROLLBACK_STATUSES:
        raise NewsletterValidationError(f"A {profile.status.lower()} automation profile cannot be rolled back to")
    current_active = db.scalar(
        select(NewsletterAutomationProfile).where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter_id,
            NewsletterAutomationProfile.status == "ACTIVE",
        )
    )
    if current_active and current_active.id == profile.id:
        return _profile_detail_response(profile)
    if current_active:
        current_active.status = "SUPERSEDED"
        current_active.updated_at = utcnow()
    profile.status = "ACTIVE"
    profile.promoted_at = utcnow()
    newsletter.status = "ACTIVE"
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter_profile.rolled_back",
        "newsletter_automation_profile",
        profile.id,
        metadata={"newsletter_id": newsletter_id, "version": profile.version, "superseded_version": current_active.version if current_active else None},
    )
    db.commit()
    return _profile_detail_response(profile)


# ---------------------------------------------------------------------------
# Subscriptions (Phase 1: reads/filters only, no signup execution)
# ---------------------------------------------------------------------------

_SUBSCRIPTION_JOIN = (
    select(
        NewsletterSubscription,
        GmailAccount,
        AccountGroup,
        Newsletter,
        NewsletterGroup,
    )
    .join(GmailAccount, NewsletterSubscription.account_id == GmailAccount.id)
    .join(AccountGroup, GmailAccount.group_id == AccountGroup.id)
    .join(Newsletter, NewsletterSubscription.newsletter_id == Newsletter.id)
    .join(NewsletterGroup, Newsletter.group_id == NewsletterGroup.id)
)


def _subscription_response(rows: tuple) -> dict:
    subscription, account, account_group, newsletter, newsletter_group = rows
    return {
        "id": subscription.id,
        "account_id": account.id,
        "account_email": account.email,
        "account_group_id": account_group.id,
        "account_group_name": account_group.name,
        "newsletter_id": newsletter.id,
        "newsletter_name": newsletter.name,
        "newsletter_group_id": newsletter_group.id,
        "newsletter_group_name": newsletter_group.name,
        "state": subscription.state,
        "submitted_at": subscription.submitted_at,
        "confirmed_at": subscription.confirmed_at,
        "last_checked_at": subscription.last_checked_at,
        "last_error": subscription.last_error,
        "created_at": subscription.created_at,
        "updated_at": subscription.updated_at,
    }


def list_newsletter_subscriptions(
    db: Session,
    organization_id: str,
    account_id: str | None = None,
    newsletter_id: str | None = None,
    state: str | None = None,
    account_group_ids: set[str] | None = None,
    limit: int = 200,
) -> tuple[list[dict], int]:
    if newsletter_id is not None:
        _get_newsletter(db, organization_id, newsletter_id)
    query = _SUBSCRIPTION_JOIN.where(NewsletterSubscription.organization_id == organization_id)
    if account_id:
        query = query.where(NewsletterSubscription.account_id == account_id)
    if newsletter_id:
        query = query.where(NewsletterSubscription.newsletter_id == newsletter_id)
    if state:
        query = query.where(NewsletterSubscription.state == state)
    if account_group_ids is not None:
        query = query.where(GmailAccount.group_id.in_(account_group_ids))
    total = len(db.execute(query).all())
    rows = db.execute(query.order_by(NewsletterSubscription.created_at.desc()).limit(limit)).all()
    return [_subscription_response(row) for row in rows], total


def get_newsletter_subscription(
    db: Session,
    organization_id: str,
    subscription_id: str,
    account_group_ids: set[str] | None = None,
) -> dict | None:
    query = _SUBSCRIPTION_JOIN.where(
        NewsletterSubscription.organization_id == organization_id, NewsletterSubscription.id == subscription_id
    )
    row = db.execute(query).one_or_none()
    if not row:
        return None
    if account_group_ids is not None and row[1].group_id not in account_group_ids:
        return None
    return _subscription_response(row)


# ---------------------------------------------------------------------------
# Jobs (Phase 1: definition create/list/get only; never dispatch)
# ---------------------------------------------------------------------------


def newsletter_job_response(db: Session, job: NewsletterJob, include_tasks: bool = False) -> dict:
    account_group = db.get(AccountGroup, job.account_group_id)
    newsletter = db.get(Newsletter, job.newsletter_id) if job.newsletter_id else None
    newsletter_group = db.get(NewsletterGroup, job.newsletter_group_id) if job.newsletter_group_id else None
    total_tasks = (
        db.scalar(select(func.count(NewsletterTask.id)).where(NewsletterTask.organization_id == job.organization_id, NewsletterTask.job_id == job.id))
        or 0
    )
    task_counts = dict(
        db.execute(
            select(NewsletterTask.state, func.count(NewsletterTask.id))
            .where(NewsletterTask.organization_id == job.organization_id, NewsletterTask.job_id == job.id)
            .group_by(NewsletterTask.state)
        ).all()
    )
    tasks: list[dict] = []
    if include_tasks:
        task_rows = db.execute(
            select(NewsletterTask, GmailAccount, Newsletter)
            .join(GmailAccount, NewsletterTask.account_id == GmailAccount.id)
            .join(Newsletter, NewsletterTask.newsletter_id == Newsletter.id)
            .where(NewsletterTask.organization_id == job.organization_id, NewsletterTask.job_id == job.id)
            .order_by(NewsletterTask.created_at)
        ).all()
        for task, account, target in task_rows:
            tasks.append(
                {
                    "id": task.id,
                    "account_id": task.account_id,
                    "account_email": account.email,
                    "newsletter_id": task.newsletter_id,
                    "newsletter_name": target.name,
                    "state": task.state,
                    "attempts": task.attempts,
                    "profile_version": task.profile_version,
                    "worker_id": task.worker_id,
                    "browser_session_id": task.browser_session_id,
                    "heartbeat_at": task.heartbeat_at,
                    "last_error": task.last_error,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                }
            )
    retry_policy = dict(job.retry_policy_json or {})
    random_newsletter_count = retry_policy.get("random_newsletter_count")
    if not isinstance(random_newsletter_count, int) or isinstance(random_newsletter_count, bool) or random_newsletter_count < 1:
        random_newsletter_count = 0
    return {
        "id": job.id,
        "organization_id": job.organization_id,
        "newsletter_id": job.newsletter_id,
        "newsletter_name": newsletter.name if newsletter else None,
        "newsletter_group_id": job.newsletter_group_id,
        "newsletter_group_name": newsletter_group.name if newsletter_group else None,
        "account_group_id": job.account_group_id,
        "account_group_name": account_group.name if account_group else None,
        "status": job.status,
        "concurrency_limit": job.concurrency_limit,
        "retry_policy_json": dict(job.retry_policy_json or {}),
        "randomize_newsletters": random_newsletter_count > 0,
        "random_newsletter_count": random_newsletter_count or None,
        "auto_confirm": job.auto_confirm,
        "recipient_mode": "EXTERNAL" if _external_recipients(job) else "SAVED_ACCOUNTS",
        "external_recipient_count": len(_external_recipients(job)),
        "headless_browser": bool(retry_policy.get("headless_browser", False)) if _external_recipients(job) else False,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "updated_at": job.updated_at,
        "total_tasks": total_tasks,
        "task_counts": task_counts,
        "tasks": tasks,
    }


def create_newsletter_job(
    db: Session,
    organization_id: str,
    actor_id: str,
    newsletter_id: str | None,
    newsletter_group_id: str | None,
    account_group_id: str,
    concurrency_limit: int,
    retry_policy_json: dict,
    auto_confirm: bool,
) -> dict:
    if isinstance(concurrency_limit, bool) or not 1 <= concurrency_limit <= 10:
        raise NewsletterValidationError("Newsletter concurrency must be between 1 and 10 sessions")
    if not newsletter_id and not newsletter_group_id:
        raise NewsletterValidationError("Choose a newsletter or a newsletter group to target")
    if newsletter_id:
        _get_newsletter(db, organization_id, newsletter_id)
    if newsletter_group_id:
        _get_newsletter_group(db, organization_id, newsletter_group_id)
    normalized_retry_policy = dict(retry_policy_json or {})
    authenticated_only = normalized_retry_policy.get("authenticated_only", False)
    if not isinstance(authenticated_only, bool):
        raise NewsletterValidationError("authenticated_only must be true or false")
    if authenticated_only:
        normalized_retry_policy["authenticated_only"] = True
    else:
        normalized_retry_policy.pop("authenticated_only", None)
    account_ids = normalized_retry_policy.get("account_ids")
    if account_ids is not None:
        if not isinstance(account_ids, list) or not account_ids or len(account_ids) > 5000 or any(not isinstance(item, str) or not item.strip() for item in account_ids):
            raise NewsletterValidationError("Choose between 1 and 5000 saved accounts")
        normalized_account_ids = list(dict.fromkeys(item.strip() for item in account_ids))
        scoped_accounts = db.scalars(
            select(GmailAccount.id).where(
                GmailAccount.organization_id == organization_id,
                GmailAccount.group_id == account_group_id,
                GmailAccount.id.in_(normalized_account_ids),
                GmailAccount.lifecycle_status != "ARCHIVED",
                GmailAccount.account_type != "EXTERNAL_RECIPIENT",
            )
        ).all()
        if len(scoped_accounts) != len(normalized_account_ids):
            raise NewsletterValidationError("One or more selected accounts are unavailable in this account group")
        normalized_retry_policy["account_ids"] = normalized_account_ids
    bulk_limit = normalized_retry_policy.get("bulk_concurrency_limit")
    if bulk_limit is not None and (isinstance(bulk_limit, bool) or not isinstance(bulk_limit, int) or not 1 <= bulk_limit <= 10):
        raise NewsletterValidationError("Bulk-run concurrency must be between 1 and 10 sessions")
    configured_group_ids = normalized_retry_policy.get("newsletter_group_ids")
    if configured_group_ids is not None:
        if not isinstance(configured_group_ids, list) or not configured_group_ids or len(configured_group_ids) > 50:
            raise NewsletterValidationError("Choose between 1 and 50 newsletter groups")
        normalized_group_ids: list[str] = []
        for value in configured_group_ids:
            group_id = str(value).strip()
            if not group_id or group_id in normalized_group_ids:
                continue
            _get_newsletter_group(db, organization_id, group_id)
            normalized_group_ids.append(group_id)
        if not normalized_group_ids:
            raise NewsletterValidationError("Choose at least one newsletter group")
        if newsletter_group_id and newsletter_group_id not in normalized_group_ids:
            normalized_group_ids.insert(0, newsletter_group_id)
        normalized_retry_policy["newsletter_group_ids"] = normalized_group_ids
    recipient_mode = normalized_retry_policy.get("recipient_mode", "SAVED_ACCOUNTS")
    if recipient_mode not in {"SAVED_ACCOUNTS", "EXTERNAL"}:
        raise NewsletterValidationError("Recipient mode is not supported")
    if recipient_mode == "EXTERNAL":
        if normalized_retry_policy.get("consent_confirmed") is not True:
            raise NewsletterValidationError("Confirm that you own or have permission to subscribe these external addresses")
        recipients = _external_recipients(
            NewsletterJob(retry_policy_json=normalized_retry_policy)
        )
        if not recipients or len(recipients) > 300:
            raise NewsletterValidationError("Enter between 1 and 300 external email addresses")
        normalized_retry_policy["external_recipients"] = recipients
        normalized_retry_policy["consent_confirmed"] = True
        # External addresses have no saved Gmail profile.  Headless is the
        # safe default for their disposable browser context, while operators
        # may explicitly request a visible browser for troubleshooting.
        headless_browser = normalized_retry_policy.get("headless_browser", True)
        if not isinstance(headless_browser, bool):
            raise NewsletterValidationError("headless_browser must be true or false")
        normalized_retry_policy["headless_browser"] = headless_browser
        auto_confirm = False
    else:
        normalized_retry_policy.pop("external_recipients", None)
        normalized_retry_policy.pop("consent_confirmed", None)
        normalized_retry_policy.pop("headless_browser", None)
    random_newsletter_count = normalized_retry_policy.get("random_newsletter_count")
    randomize_newsletters = normalized_retry_policy.get("randomize_newsletters") is True or isinstance(random_newsletter_count, int)
    if randomize_newsletters:
        if isinstance(random_newsletter_count, bool) or not isinstance(random_newsletter_count, int) or not 1 <= random_newsletter_count <= 50:
            raise NewsletterValidationError("Random newsletter count must be between 1 and 50")
        normalized_retry_policy["randomize_newsletters"] = True
        normalized_retry_policy["random_newsletter_count"] = random_newsletter_count
    else:
        normalized_retry_policy.pop("randomize_newsletters", None)
        normalized_retry_policy.pop("random_newsletter_count", None)
    account_group = db.scalar(select(AccountGroup).where(AccountGroup.organization_id == organization_id, AccountGroup.id == account_group_id))
    if not account_group:
        raise NewsletterValidationError("Choose a valid account group")
    job = NewsletterJob(
        id=_new_id("nljob"),
        organization_id=organization_id,
        newsletter_id=newsletter_id,
        newsletter_group_id=newsletter_group_id,
        account_group_id=account_group_id,
        status="DRAFT",
        concurrency_limit=concurrency_limit,
        retry_policy_json=normalized_retry_policy,
        auto_confirm=auto_confirm,
        created_by=actor_id,
    )
    db.add(job)
    db.flush()
    _audit(
        db,
        organization_id,
        actor_id,
        "newsletter_job.created",
        "newsletter_job",
        job.id,
        metadata={
            "newsletter_id": newsletter_id,
            "newsletter_group_id": newsletter_group_id,
            "account_group_id": account_group_id,
            "concurrency_limit": concurrency_limit,
            "auto_confirm": auto_confirm,
            "randomize_newsletters": normalized_retry_policy.get("randomize_newsletters", False),
            "random_newsletter_count": normalized_retry_policy.get("random_newsletter_count"),
            "recipient_mode": recipient_mode,
            "external_recipient_count": len(normalized_retry_policy.get("external_recipients", [])) if recipient_mode == "EXTERNAL" else 0,
            "headless_browser": normalized_retry_policy.get("headless_browser") if recipient_mode == "EXTERNAL" else None,
        },
    )
    db.commit()
    return newsletter_job_response(db, job)


def list_newsletter_jobs(
    db: Session,
    organization_id: str,
    account_group_ids: set[str] | None = None,
    limit: int = 50,
    created_by: str | None = None,
) -> tuple[list[dict], int]:
    query = select(NewsletterJob).where(NewsletterJob.organization_id == organization_id)
    if account_group_ids is not None:
        query = query.where(NewsletterJob.account_group_id.in_(account_group_ids))
    if created_by is not None:
        query = query.where(NewsletterJob.created_by == created_by)
    total = len(db.scalars(query).all())
    rows = db.scalars(query.order_by(NewsletterJob.created_at.desc()).limit(limit)).all()
    return [newsletter_job_response(db, row) for row in rows], total


def get_newsletter_job(
    db: Session,
    organization_id: str,
    job_id: str,
    account_group_ids: set[str] | None = None,
    created_by: str | None = None,
) -> dict | None:
    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        return None
    if account_group_ids is not None and job.account_group_id not in account_group_ids:
        return None
    if created_by is not None and job.created_by != created_by:
        return None
    return newsletter_job_response(db, job, include_tasks=True)


def delete_newsletter_job_history(db: Session, organization_id: str, actor_id: str, job_id: str) -> None:
    """Remove a finished run and its task events, preserving subscriptions."""
    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id).with_for_update())
    if job is None:
        raise NewsletterValidationError("Newsletter job not found")
    if job.status not in {"DRAFT", "COMPLETED", "CANCELLED", "FAILED"}:
        raise NewsletterValidationError("Stop the newsletter job before deleting its history")
    if any(task.state == "PROCESSING" for task in job.tasks):
        raise NewsletterValidationError("This job still has an active browser task")
    if any(task.browser_session_id and db.scalar(select(BrowserSession.id).where(BrowserSession.id == task.browser_session_id, BrowserSession.state != "CLOSED")) for task in job.tasks):
        raise NewsletterValidationError("Close the job's browser sessions before deleting its history")
    count = len(job.tasks)
    db.delete(job)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="newsletter_job.history_deleted", target_type="newsletter_job", target_id=job_id, result="success", metadata_json={"task_count": count}))
    db.commit()


def delete_newsletter_job_histories(db: Session, organization_id: str, actor_id: str, job_ids: list[str]) -> int:
    """Atomically remove eligible job histories while preserving subscriptions."""
    jobs = db.scalars(
        select(NewsletterJob)
        .where(NewsletterJob.organization_id == organization_id, NewsletterJob.id.in_(job_ids))
        .with_for_update()
    ).all()
    if len(jobs) != len(job_ids):
        raise NewsletterValidationError("One or more newsletter jobs were not found")
    for job in jobs:
        if job.status not in {"DRAFT", "COMPLETED", "CANCELLED", "FAILED"}:
            raise NewsletterValidationError(f"Stop newsletter job {job.id} before deleting its history")
        if any(task.state == "PROCESSING" for task in job.tasks):
            raise NewsletterValidationError(f"Newsletter job {job.id} still has an active browser task")
        if any(task.browser_session_id and db.scalar(select(BrowserSession.id).where(BrowserSession.id == task.browser_session_id, BrowserSession.state != "CLOSED")) for task in job.tasks):
            raise NewsletterValidationError(f"Close newsletter job {job.id}'s browser sessions before deleting its history")
    for job in jobs:
        task_count = len(job.tasks)
        db.delete(job)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="newsletter_job.history_deleted", target_type="newsletter_job", target_id=job.id, result="success", metadata_json={"task_count": task_count, "bulk": True}))
    db.commit()
    return len(jobs)


# ---------------------------------------------------------------------------
# Phase 8: bounded bulk execution / Phase 10: health-gated recovery
# ---------------------------------------------------------------------------


NEWSLETTER_TASK_ACTIVE = {"PROCESSING"}
NEWSLETTER_TASK_RESOLVED = {"SUBMITTED", "CONFIRMED", "SKIPPED", "FAILED", "MANUAL_REVIEW", "CANCELLED"}


def _job_targets(db: Session, job: NewsletterJob) -> list[Newsletter]:
    executable_statuses = {"ACTIVE", "TEST_REQUIRED"}
    if job.newsletter_id:
        target = db.get(Newsletter, job.newsletter_id)
        return [target] if target and target.status in executable_statuses and target.discovery_status not in {"FAILED", "DISCOVERY_BLOCKED"} else []
    configured_group_ids = (job.retry_policy_json or {}).get("newsletter_group_ids")
    group_ids = [str(value).strip() for value in configured_group_ids if str(value).strip()] if isinstance(configured_group_ids, list) else []
    if not group_ids and job.newsletter_group_id:
        group_ids = [job.newsletter_group_id]
    if not group_ids:
        return []
    return db.scalars(
        select(Newsletter)
        .where(
            Newsletter.organization_id == job.organization_id,
            Newsletter.group_id.in_(list(dict.fromkeys(group_ids))),
            Newsletter.status.in_(executable_statuses),
            Newsletter.discovery_status.not_in({"FAILED", "DISCOVERY_BLOCKED"}),
        )
        .order_by(Newsletter.created_at)
    ).all()


def _active_profile(db: Session, organization_id: str, newsletter_id: str) -> NewsletterAutomationProfile | None:
    return db.scalar(
        select(NewsletterAutomationProfile)
        .where(
            NewsletterAutomationProfile.organization_id == organization_id,
            NewsletterAutomationProfile.newsletter_id == newsletter_id,
            NewsletterAutomationProfile.status == "ACTIVE",
        )
        .order_by(NewsletterAutomationProfile.version.desc())
    )


def _job_task_counts(db: Session, job_id: str) -> dict[str, int]:
    return dict(
        db.execute(
            select(NewsletterTask.state, func.count(NewsletterTask.id))
            .where(NewsletterTask.job_id == job_id)
            .group_by(NewsletterTask.state)
        ).all()
    )


def _newsletter_queue_entries(rows: list[NewsletterTask]) -> list[tuple[str, str, float]]:
    """Interleave queued tasks so every account reaches the front of the queue.

    Materialization walks accounts outer and newsletters inner, so the natural
    ``created_at`` order is one account's entire backlog before the next one.
    Rotate both dimensions: give every account its first website before any
    account gets its second, then every account its second before any gets its
    third. This fills each concurrency wave with distinct accounts while
    rotating publisher sites consistently across the selected account scope.
    """
    by_account: dict[str, list[NewsletterTask]] = {}
    for row in rows:
        by_account.setdefault(row.account_id, []).append(row)
    for bucket in by_account.values():
        bucket.sort(key=lambda row: (row.created_at, row.id))
    account_ids = sorted(
        by_account,
        key=lambda account_id: (by_account[account_id][0].created_at, account_id),
    )
    base = min(row.created_at.timestamp() for row in rows)
    entries: list[tuple[str, str, float]] = []
    depth = max((len(bucket) for bucket in by_account.values()), default=0)
    for index in range(depth):
        # Keep every score strictly increasing so Redis' sorted set stays a
        # faithful FIFO within the interleaved order.
        for account_id in account_ids:
            bucket = by_account[account_id]
            if index >= len(bucket):
                continue
            row = bucket[index]
            entries.append((row.id, row.account_id, base + len(entries) * 0.000001))
    return entries


def _enqueue_newsletter_tasks(db: Session, job: NewsletterJob) -> int:
    """Mirror the durable queue into the existing Redis priority queue."""
    from .worker.queue import RedisQueue

    rows = db.scalars(
        select(NewsletterTask).where(
            NewsletterTask.job_id == job.id,
            NewsletterTask.state == "QUEUED",
        )
    ).all()
    if job.auto_confirm:
        rows += db.scalars(
            select(NewsletterTask).where(
                NewsletterTask.job_id == job.id,
                NewsletterTask.state == "WAITING_CONFIRMATION",
                NewsletterTask.browser_session_id.is_(None),
            )
        ).all()
    if rows:
        # Additive rehydration: never overwrite the score of a task that is
        # already queued, or a scheduled retry delay would be lost.
        RedisQueue().ensure_members(job.id, _newsletter_queue_entries(rows))
    return len(rows)


def _record_task_event(db: Session, task: NewsletterTask, event: str, result: str = "success", detail: str | None = None) -> None:
    db.add(
        NewsletterTaskEvent(
            id=_new_id("nltevent"),
            organization_id=task.organization_id,
            task_id=task.id,
            event=event,
            result=result,
            detail=(detail or "")[:500] or None,
        )
    )


def materialize_newsletter_job(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        raise NewsletterValidationError("Newsletter job not found")
    target_pool = _job_targets(db, job)
    if not target_pool:
        raise NewsletterValidationError("The job has no available newsletters to execute")
    retry_policy = dict(job.retry_policy_json or {})
    random_newsletter_count = retry_policy.get("random_newsletter_count")
    if not isinstance(random_newsletter_count, int) or isinstance(random_newsletter_count, bool) or random_newsletter_count < 1:
        random_newsletter_count = 0
    external_recipients = _external_recipients(job)
    if external_recipients:
        accounts = [
            _ensure_external_recipient_account(db, organization_id, job.account_group_id, email)
            for email in external_recipients
        ]
    else:
        account_filters = [
                GmailAccount.organization_id == organization_id,
                GmailAccount.group_id == job.account_group_id,
                GmailAccount.lifecycle_status != "ARCHIVED",
                GmailAccount.account_type != "EXTERNAL_RECIPIENT",
        ]
        selected_ids = retry_policy.get("account_ids")
        if isinstance(selected_ids, list) and selected_ids:
            account_filters.append(GmailAccount.id.in_(selected_ids))
        if retry_policy.get("authenticated_only") is True:
            account_filters.extend((GmailAccount.login_status == "AUTHENTICATED", GmailAccount.profile_status == "SAVED"))
        accounts = db.scalars(select(GmailAccount).where(*account_filters).order_by(GmailAccount.created_at)).all()
    if not accounts:
        if retry_policy.get("authenticated_only") is True:
            raise NewsletterValidationError("No authenticated accounts with saved browser profiles match this job")
        raise NewsletterValidationError("The selected account scope has no active accounts")
    for account in accounts:
        account_targets = target_pool
        if random_newsletter_count:
            sample_size = min(random_newsletter_count, len(target_pool))
            # A stable per-job/per-account seed makes the random-looking choice
            # reproducible when an operator inspects or retries the job.
            selector = random.Random(f"{job.id}:{account.id}")
            account_targets = selector.sample(target_pool, sample_size)
        for target in account_targets:
            existing = db.scalar(
                select(NewsletterTask).where(
                    NewsletterTask.job_id == job.id,
                    NewsletterTask.account_id == account.id,
                    NewsletterTask.newsletter_id == target.id,
                )
            )
            if existing:
                continue
            profile = _active_profile(db, organization_id, target.id)
            subscription = db.scalar(
                select(NewsletterSubscription).where(
                    NewsletterSubscription.organization_id == organization_id,
                    NewsletterSubscription.account_id == account.id,
                    NewsletterSubscription.newsletter_id == target.id,
                )
            )
            if not profile:
                # Imported TEST_REQUIRED records remain valid bulk targets.
                # The signup service can use its bounded live-form resolver
                # without a saved profile; only an actual resolver failure is
                # converted to manual review by the observer.
                state, error, version = "QUEUED", None, None
            elif subscription and subscription.state == "CONFIRMED":
                state, error, version = "SKIPPED", "Already confirmed; duplicate signup skipped", profile.version
            elif subscription and subscription.state in {"SUBMITTED", "WAITING_CONFIRMATION"}:
                state, error, version = ("WAITING_CONFIRMATION" if job.auto_confirm else "SUBMITTED"), "Existing subscription is already awaiting confirmation", profile.version
            else:
                state, error, version = "QUEUED", None, profile.version
            task = NewsletterTask(
                id=_new_id("nltask"),
                organization_id=organization_id,
                job_id=job.id,
                account_id=account.id,
                newsletter_id=target.id,
                state=state,
                profile_version=version,
                last_error=error,
            )
            db.add(task)
            db.flush()
            _record_task_event(db, task, "materialized", "success" if state in {"QUEUED", "SUBMITTED", "WAITING_CONFIRMATION"} else "skipped", error)
    db.add(
        AuditEvent(
            id=_new_id("audit"),
            organization_id=organization_id,
            actor_id=actor_id,
            action="newsletter_job.tasks_materialized",
            target_type="newsletter_job",
            target_id=job.id,
            result="success",
            metadata_json={
                "task_count": db.scalar(select(func.count(NewsletterTask.id)).where(NewsletterTask.job_id == job.id)) or 0,
                "randomize_newsletters": random_newsletter_count > 0,
                "random_newsletter_count": random_newsletter_count or None,
            },
        )
    )
    db.flush()
    return newsletter_job_response(db, job, include_tasks=True)


def _release_newsletter_task_lease(db: Session, task: NewsletterTask, reason: str = "session_finalized") -> None:
    from .service import _locks
    from .worker.queue import RedisQueue

    locks = _locks()
    if task.lock_key:
        locks.release(task.account_id, task.lock_key)
    queue = RedisQueue()
    # An old task must not erase a newer session's capacity marker when its
    # token expired and the account was acquired again.
    if not locks.held(task.account_id):
        queue.remove_active_profile(task.account_id)
    queue.remove_running(task.job_id, task.account_id)
    job = task.job
    bulk_scope = str((job.retry_policy_json or {}).get("bulk_run_id") or job.id)
    queue.remove_newsletter_bulk_running(bulk_scope, task.id)
    if task.browser_session_id:
        lock_row = db.scalar(
            select(JobLock).where(
                JobLock.released == False,
                JobLock.account_id == task.account_id,
                JobLock.session_id == task.browser_session_id,
            )
        )
        if lock_row:
            lock_row.released = True
            lock_row.released_at = utcnow()
            lock_row.reason = reason
            lock_row.heartbeat_at = None
    task.lock_key = None
    task.worker_id = None
    task.heartbeat_at = None


def _newsletter_retry_limit(job: NewsletterJob) -> int:
    value = (job.retry_policy_json or {}).get("max_attempts", 3)
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return 3


def _newsletter_job_complete(db: Session, job: NewsletterJob) -> None:
    counts = _job_task_counts(db, job.id)
    unresolved_states = {"QUEUED", "PROCESSING", "RETRY_PENDING"}
    if job.auto_confirm:
        unresolved_states.add("WAITING_CONFIRMATION")
    unresolved = sum(counts.get(state, 0) for state in unresolved_states)
    if job.status == "RUNNING" and unresolved == 0 and counts:
        job.status = "COMPLETED"
        job.completed_at = utcnow()
        # Drop only job-scoped mirrors. Other jobs may still share this bulk
        # scope and their task members must keep occupying its slots.
        from .worker.queue import RedisQueue

        queue = RedisQueue()
        queue.clear(job.id)
        queue.clear_running(job.id)


# Seconds a task is parked when its account is already busy (another job or a
# live operator session holds the lease). Long enough to let that session end,
# short enough that the task still lands in the next wave.
_NEWSLETTER_DEFERRED_SECONDS = 15.0

# Extra queue entries a wave may examine beyond the number of distinct accounts,
# so a wave can skip stale/foreign members without spinning.
_NEWSLETTER_WAVE_EXAMINATION_SLACK = 64


def _newsletter_wave_examination_budget(job: NewsletterJob, bulk_limit: int) -> int:
    """Cap how many queued entries one dispatch wave may inspect.

    The wave stops as soon as every concurrency slot is filled, so this budget
    only matters when many entries are unusable (locked accounts, already
    leased accounts, finished tasks). It keeps a 2,000+ task backlog from
    turning a single tick into an unbounded scan.
    """
    return max(int(bulk_limit), 1) * 8 + _NEWSLETTER_WAVE_EXAMINATION_SLACK


def _newsletter_resource_aware_limit(
    configured_limit: int,
    *,
    available_memory_bytes: int | None = None,
    cpu_count: int | None = None,
    load_average: float | None = None,
) -> int:
    """Throttle only new browser launches when host resources are tight.

    Existing sessions are never interrupted. Linux memory readings respect
    cgroup limits when present; callers may inject readings for deterministic
    tests. Reserve 1.5 GiB for the app/desktop and budget 768 MiB per additional
    browser, then leave CPU headroom based on the one-minute load average.
    Unsupported platforms retain the operator's configured limit.
    """
    limit = max(1, min(10, int(configured_limit)))
    try:
        if available_memory_bytes is None:
            available_memory_bytes = _newsletter_available_memory_bytes()
        if available_memory_bytes is not None:
            reserve = 1536 * 1024 * 1024
            browser_budget = 768 * 1024 * 1024
            memory_slots = max(1, (available_memory_bytes - reserve) // browser_budget)
            limit = min(limit, int(memory_slots))
        if cpu_count is None:
            cpu_count = os.cpu_count()
        if load_average is None and hasattr(os, "getloadavg"):
            load_average = os.getloadavg()[0]
        if cpu_count and cpu_count > 0 and load_average is not None:
            cpu_slots = max(1, cpu_count - math.ceil(max(0.0, load_average)))
            limit = min(limit, cpu_slots)
    except (OSError, ValueError, TypeError):
        # Resource sensing must never block work or turn a valid job into an
        # error; on unfamiliar hosts the configured bounded limit applies.
        return max(1, min(10, int(configured_limit)))
    return max(1, limit)


def _newsletter_available_memory_bytes() -> int | None:
    """Return memory available to this process, respecting Linux cgroups."""
    available = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    for limit_path, usage_path in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        try:
            raw_limit = Path(limit_path).read_text(encoding="utf-8").strip()
            if raw_limit == "max":
                continue
            limit = int(raw_limit)
            usage = int(Path(usage_path).read_text(encoding="utf-8").strip())
            if 0 < limit < (1 << 60):
                cgroup_available = max(0, limit - usage)
                available = cgroup_available if available is None else min(available, cgroup_available)
        except (OSError, ValueError):
            continue
    return available


def _active_browser_account_count(db: Session, organization_id: str) -> int:
    """Durable backstop for the Redis active-profile capacity mirror."""
    return int(db.scalar(
        select(func.count(func.distinct(BrowserSession.account_id))).where(
            BrowserSession.organization_id == organization_id,
            BrowserSession.state.in_({"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}),
        )
    ) or 0)


def _newsletter_scope_processing_count(db: Session, organization_id: str, scope: str) -> int:
    """Count live task leases durably for all jobs in a shared bulk action."""
    jobs = db.scalars(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id)).all()
    job_ids = [
        item.id for item in jobs
        if str((item.retry_policy_json or {}).get("bulk_run_id") or item.id) == scope
    ]
    if not job_ids:
        return 0
    return int(db.scalar(select(func.count()).select_from(NewsletterTask).where(
        NewsletterTask.job_id.in_(job_ids),
        NewsletterTask.state == "PROCESSING",
    )) or 0)


def _newsletter_job_processing_count(db: Session, job_id: str) -> int:
    return int(db.scalar(select(func.count()).select_from(NewsletterTask).where(
        NewsletterTask.job_id == job_id,
        NewsletterTask.state == "PROCESSING",
    )) or 0)


def _wake_newsletter_dispatcher() -> None:
    """Ask the local worker to refill a slot as soon as its current tick ends."""
    try:
        from .worker.worker import get_login_worker

        get_login_worker().wake()
    except Exception:  # noqa: BLE001 - dispatch can still recover on its normal tick
        logger.debug("could not wake newsletter dispatcher", exc_info=True)


def _reusable_newsletter_session(db: Session, job: NewsletterJob, account_id: str) -> BrowserSession | None:
    """Return this job/account's parked browser when its next signup can reuse it."""
    profile = db.scalar(select(BrowserProfile).where(BrowserProfile.account_id == account_id))
    session_id = profile.active_session_id if profile else None
    if not session_id:
        return None
    session = db.scalar(select(BrowserSession).where(
        BrowserSession.id == session_id,
        BrowserSession.organization_id == job.organization_id,
        BrowserSession.account_id == account_id,
        BrowserSession.state == "RUNNING",
    ))
    metadata = (session.metadata_json or {}) if session else {}
    if (session is None or metadata.get("purpose") != "newsletter"
            or metadata.get("newsletter_job_id") != job.id
            or not metadata.get("newsletter_session_reuse_ready")):
        return None
    from .service import _observers

    if session.id not in _observers:
        return None
    return session


def dispatch_newsletter_job(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    """Serialize workspace newsletter slot decisions across all bulk jobs."""
    from .service import _locks, get_settings

    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        raise NewsletterValidationError("Newsletter job not found")
    if job.status != "RUNNING":
        raise NewsletterValidationError("Start the newsletter job before dispatching tasks")
    lock_account_id = "newsletter-dispatch-" + hashlib.sha256(organization_id.encode("utf-8")).hexdigest()[:32]
    locks = _locks()
    ttl_ms = max(120, get_settings().worker_lock_ttl) * 1000
    token = locks.acquire(lock_account_id, "newsletter-dispatch", "", ttl_ms)
    if not token:
        # Another API request or worker is filling slots. Wake the worker so a
        # freed slot is retried immediately after that serialized wave exits.
        if not actor_id.startswith("worker-"):
            _wake_newsletter_dispatcher()
        return newsletter_job_response(db, job, include_tasks=True)
    try:
        return _dispatch_newsletter_job_locked(db, organization_id, actor_id, job_id, lock_account_id, token, ttl_ms)
    finally:
        locks.release(lock_account_id, token)


def _dispatch_newsletter_job_locked(db: Session, organization_id: str, actor_id: str, job_id: str, dispatch_lock_id: str, dispatch_token: str, dispatch_ttl_ms: int) -> dict:
    """Dispatch one bounded wave using the Redis queue and account leases."""
    from .service import AccountSessionConflictError, BrowserLaunchError, _locks, _worker_token, get_settings, start_newsletter_confirmation_test, start_newsletter_signup_test
    from .worker.queue import RedisQueue

    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        raise NewsletterValidationError("Newsletter job not found")
    if job.status != "RUNNING":
        raise NewsletterValidationError("Start the newsletter job before dispatching tasks")
    queue, locks = RedisQueue(), _locks()
    # The database is the durable source of truth. Redis is only a dispatch
    # mirror and can be flushed, restarted, or lose a key between the start
    # request and the worker's next tick. Rebuild the mirror on every dispatch
    # so a RUNNING job cannot strand QUEUED tasks indefinitely.
    _enqueue_newsletter_tasks(db, job)
    db.commit()
    job = db.get(NewsletterJob, job.id)
    worker_id = _worker_token() if actor_id.startswith("worker-") else "orchestrator-api"
    started = 0
    deferred = 0
    paused_all_busy = False
    bulk_scope = str((job.retry_policy_json or {}).get("bulk_run_id") or job.id)
    external_recipient_job = bool(_external_recipients(job))
    headless_browser = bool((job.retry_policy_json or {}).get("headless_browser", True)) if external_recipient_job else False
    bulk_limit_raw = (job.retry_policy_json or {}).get("bulk_concurrency_limit", job.concurrency_limit)
    try:
        bulk_limit = max(1, min(10, int(bulk_limit_raw)))
    except (TypeError, ValueError):
        bulk_limit = job.concurrency_limit
    runtime_settings = get_settings()
    # The selected concurrency is the operating target. Respect the separate
    # administrator cap and product maximum, but do not silently shrink the
    # wave based on momentary load estimates; that made a configured run look
    # intermittently under-filled and delayed slot replacement.
    resource_limit = max(1, min(runtime_settings.worker_max_active_profiles, 10))
    wave_limit = min(job.concurrency_limit, bulk_limit, resource_limit)
    # Accounts with a task already running in this job are already consuming
    # their one-account slot. Seed the set from durable task state: observer
    # events can invoke dispatch between signup steps, before the browser has
    # closed, and otherwise the same account's queued newsletters look like
    # external lock conflicts and incorrectly pause the whole job.
    leased_accounts: set[str] = set(
        db.scalars(
            select(NewsletterTask.account_id).where(
                NewsletterTask.job_id == job.id,
                NewsletterTask.state == "PROCESSING",
            )
        ).all()
    )
    # Bound the wave so a large backlog cannot spin the dispatcher: it may look
    # at every account once plus a small margin, but never re-examine the same
    # deferred account over and over within a single tick.
    examined = 0
    examination_budget = _newsletter_wave_examination_budget(job, bulk_limit)
    # When a browser is parked between sites, move its next same-account task
    # to the front of the queue. This preserves account/website rotation while
    # ensuring a full host does not defer the one task that can reuse an open
    # browser without consuming another slot.
    ready_sessions = db.scalars(select(BrowserSession).where(
        BrowserSession.organization_id == organization_id,
        BrowserSession.state == "RUNNING",
    )).all()
    for ready_session in ready_sessions:
        if _reusable_newsletter_session(db, job, ready_session.account_id) is None:
            continue
        next_same_account = db.scalar(select(NewsletterTask).where(
            NewsletterTask.job_id == job.id,
            NewsletterTask.account_id == ready_session.account_id,
            NewsletterTask.state == "QUEUED",
        ).order_by(NewsletterTask.created_at, NewsletterTask.id).limit(1))
        if next_same_account:
            queue.requeue(job.id, next_same_account.id, next_same_account.account_id, when=time.time() - 60)
    while examined < examination_budget:
        # Redis mirrors are useful for quick coordination, but task state in
        # the database is authoritative. This prevents stale/missing Redis
        # members from either exceeding the user's concurrency setting or
        # leaving a finished slot unused.
        if _newsletter_job_processing_count(db, job.id) >= wave_limit:
            break
        if _newsletter_scope_processing_count(db, organization_id, bulk_scope) >= bulk_limit:
            break
        if not locks.refresh(dispatch_lock_id, dispatch_token, dispatch_ttl_ms):
            # Do not open another browser after losing the scope lease.
            break
        examined += 1
        candidate = queue.pop_next(job.id)
        if not candidate:
            break
        task = db.get(NewsletterTask, candidate["item_id"])
        if not task or task.job_id != job.id or task.state not in {"QUEUED", "WAITING_CONFIRMATION", "RETRY_PENDING"}:
            continue
        if task.browser_session_id:
            linked_session = db.get(BrowserSession, task.browser_session_id)
            if linked_session and linked_session.state in {"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}:
                # A queued task should not own a live session. Preserve the
                # account boundary while that session is still open, then let
                # a later worker tick retry this task after it closes.
                leased_accounts.add(task.account_id)
                queue.requeue(job.id, task.id, task.account_id, when=time.time() + _NEWSLETTER_DEFERRED_SECONDS)
                deferred += 1
                continue
            # Older recovery paths could leave a closed session ID attached to
            # a queued task. The old guard silently skipped those tasks, which
            # left a run RUNNING forever despite having no browser to observe.
            task.browser_session_id = None
            task.worker_id = None
            task.heartbeat_at = None
            task.lock_key = None
            task.last_error = "Recovered stale browser-session reference; dispatching again"
            _record_task_event(db, task, "stale_session_reference_cleared", "retry", task.last_error)
        target = db.get(Newsletter, task.newsletter_id)
        profile = _active_profile(db, organization_id, task.newsletter_id)
        account = db.get(GmailAccount, task.account_id)
        if not target or not account:
            task.state = "MANUAL_REVIEW"
            task.last_error = "Newsletter or account is unavailable"
            _record_task_event(db, task, "dispatch_blocked", "attention", task.last_error)
            continue
        if task.account_id in leased_accounts:
            # This account is already settled for this wave: either it holds one
            # of the slots, or it is reserved by another job. Skipping the rest
            # of its backlog instead of aborting the wave is what lets a single
            # dispatch fill every configured concurrent slot with a different
            # account.
            deferred += 1
            continue
        token = locks.acquire(task.account_id, worker_id, "", get_settings().worker_lock_ttl * 1000)
        if not token:
            # Another job or a live operator session owns this account. Defer it
            # briefly and keep dispatching the remaining accounts; breaking here
            # would abandon every other account in the job.
            queue.requeue(job.id, task.id, task.account_id, when=time.time() + _NEWSLETTER_DEFERRED_SECONDS)
            leased_accounts.add(task.account_id)
            deferred += 1
            continue
        leased_accounts.add(task.account_id)
        reusable_session = _reusable_newsletter_session(db, job, task.account_id) if task.state != "WAITING_CONFIRMATION" else None
        if reusable_session is None and _active_browser_account_count(db, organization_id) >= resource_limit:
            locks.release(task.account_id, token)
            task.state = "QUEUED"
            task.attempts = max(0, task.attempts - 1)
            task.worker_id = None
            task.heartbeat_at = None
            task.lock_key = None
            task.last_error = "Deferred: host browser capacity is currently full"
            queue.requeue(job.id, task.id, task.account_id, when=time.time() + _NEWSLETTER_DEFERRED_SECONDS)
            _record_task_event(db, task, "resource_capacity_deferred", "retry", task.last_error)
            deferred += 1
            leased_accounts.discard(task.account_id)
            continue
        # Mark the durable task before launching the observer. Google pages
        # can complete very quickly, so the observer must never be able to
        # report a terminal event while the dispatcher still thinks this is a
        # queued task.
        confirmation_step = task.state == "WAITING_CONFIRMATION"
        task.state = "PROCESSING"
        task.attempts += 1
        task.worker_id = worker_id
        task.heartbeat_at = utcnow()
        task.lock_key = token
        task.last_error = None
        db.flush()
        try:
            subscription = db.scalar(
                select(NewsletterSubscription).where(
                    NewsletterSubscription.organization_id == organization_id,
                    NewsletterSubscription.account_id == task.account_id,
                    NewsletterSubscription.newsletter_id == task.newsletter_id,
                )
            )
            if confirmation_step:
                if not subscription:
                    raise NewsletterValidationError("Subscription record is missing")
                session = start_newsletter_confirmation_test(db, organization_id, actor_id, task.newsletter_id, task.account_id, profile.id if profile else None, subscription.id, job_id=job.id, task_id=task.id)
                mode = "confirmation"
            else:
                session = start_newsletter_signup_test(
                    db,
                    organization_id,
                    actor_id,
                    task.newsletter_id,
                    task.account_id,
                    profile.id if profile else None,
                    job_id=job.id,
                    task_id=task.id,
                    headless_browser=headless_browser,
                    reuse_session_id=reusable_session.id if reusable_session else None,
                )
                mode = "signup"
        except (BrowserLaunchError, NewsletterValidationError, ValueError) as exc:
            task.last_error = str(exc)[:500]
            task.worker_id = None
            task.heartbeat_at = None
            task.lock_key = None
            if isinstance(exc, AccountSessionConflictError):
                # A live account session is not a signup failure and must not
                # be retried in a tight loop. Release only this dispatcher's
                # lease, leave the task queued, and park this one account.
                # Pausing the whole job here would strand every other account in
                # a multi-account job, which is what made bulk runs stop early.
                locks.release(task.account_id, token)
                task.state = "QUEUED"
                task.attempts = max(0, task.attempts - 1)
                task.last_error = (
                    "Paused: this account is reserved by an active browser session. "
                    "No signup was attempted. Close or finish that session, then resume the job."
                )
                leased_accounts.add(task.account_id)
                deferred += 1
                queue.requeue(job.id, task.id, task.account_id, when=time.time() + _NEWSLETTER_DEFERRED_SECONDS)
                _record_task_event(db, task, "dispatch_paused_session_conflict", "attention", task.last_error)
                continue
            locks.release(task.account_id, token)
            # Invalid URLs, disabled records, missing profiles and other
            # validation failures cannot improve by reopening the same site.
            # Retry only a browser process launch failure, which is explicitly
            # documented as transient and leaves no session behind.
            retryable_launch_failure = isinstance(exc, BrowserLaunchError)
            if not retryable_launch_failure or task.attempts >= _newsletter_retry_limit(job):
                task.state = "FAILED"
                _record_task_event(db, task, "dispatch_failed", "failure", task.last_error)
            else:
                task.state = "RETRY_PENDING"
                _record_task_event(db, task, "dispatch_retry_pending", "retry", task.last_error)
                queue.requeue(job.id, task.id, task.account_id, when=__import__("time").time() + 2)
            continue
        # The observer can finish a fast form while this dispatcher is still
        # returning from the browser-start call.  Refresh the durable row
        # before binding the session lease; otherwise this dispatcher's stale
        # PROCESSING object can overwrite the observer's terminal SUBMITTED
        # state and make the worker launch the same signup again.
        db.expire(task)
        db.refresh(task)
        if task.state != "PROCESSING":
            # The observer completed between browser launch and lease binding.
            # Its event handler already released the task lease and recorded
            # the terminal state; never overwrite that outcome here.
            started += 1
            continue
        if not locks.bind_session(task.account_id, token, session.id):
            # The account lease expired during browser startup. Stop this
            # session rather than leaving an unlocked saved profile running.
            from .runtime import close_local_browser
            from .service import _stop_observer

            close_local_browser(session.id)
            _stop_observer(session.id)
            now = utcnow()
            session.state = "CLOSED"
            session.closed_at = now
            session.last_event_at = now
            if session.profile.active_session_id == session.id:
                session.profile.active_session_id = None
                session.profile.lock_state = "UNLOCKED"
                session.profile.status = "RESERVED"
                session.profile.last_closed_at = now
            task.state = "MANUAL_REVIEW"
            task.last_error = "The account lease expired during browser startup; signup outcome needs review"
            _release_newsletter_task_lease(db, task, "lease_lost_during_startup")
            _record_task_event(db, task, "dispatch_lease_lost", "attention", task.last_error)
            continue
        now = utcnow()
        task.profile_version = profile.version if profile else None
        task.worker_id = worker_id
        task.browser_session_id = session.id
        task.heartbeat_at = now
        task.lock_key = token
        task.last_error = None
        db.add(JobLock(id=_new_id("joblock"), organization_id=organization_id, account_id=task.account_id, profile_id=session.profile_id, session_id=session.id, worker_id=worker_id, lock_key=token, acquired_at=now, heartbeat_at=now))
        queue.add_active_profile(task.account_id)
        queue.add_running(job.id, task.account_id)
        queue.add_newsletter_bulk_running(bulk_scope, task.id)
        _record_task_event(db, task, f"{mode}_dispatched")
        started += 1
    # The session factory disables autoflush; a task whose startup conflicted
    # above has just been returned to QUEUED in memory and must not be counted
    # as still processing by this decision.
    db.flush()
    processing_count = db.scalar(
        select(func.count()).select_from(NewsletterTask).where(
            NewsletterTask.job_id == job.id,
            NewsletterTask.state == "PROCESSING",
        )
    ) or 0
    if started == 0 and deferred and not processing_count:
        # Every account this wave could reach was busy, so the job cannot make
        # progress on its own. Pause it and say why, instead of spinning tick
        # after tick. A multi-account job where some account did start keeps
        # running, which is what prevents a single reserved account from
        # stopping an otherwise healthy bulk run.
        still_queued = db.scalar(
            select(func.count())
            .select_from(NewsletterTask)
            .where(
                NewsletterTask.job_id == job.id,
                NewsletterTask.state.in_(("QUEUED", "RETRY_PENDING")),
            )
        )
        if still_queued:
            job.status = "PAUSED"
            paused_all_busy = True
    _newsletter_job_complete(db, job)
    db.add(
        AuditEvent(
            id=_new_id("audit"),
            organization_id=organization_id,
            actor_id=actor_id,
            action="newsletter_job.dispatched",
            target_type="newsletter_job",
            target_id=job.id,
            result="success",
            metadata_json={
                "opened_count": started,
                "deferred_count": deferred,
                "paused_all_accounts_busy": paused_all_busy,
                "concurrency_limit": job.concurrency_limit,
                "bulk_concurrency_limit": bulk_limit,
                "resource_concurrency_limit": resource_limit,
                "effective_wave_limit": wave_limit,
            },
        )
    )
    db.commit()
    return newsletter_job_response(db, job, include_tasks=True)


def start_newsletter_job(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        raise NewsletterValidationError("Newsletter job not found")
    if job.status not in {"DRAFT", "PAUSED"}:
        raise NewsletterValidationError(f"Cannot start a {job.status.lower()} newsletter job")
    materialize_newsletter_job(db, organization_id, actor_id, job.id)
    job = db.get(NewsletterJob, job.id)
    job.status = "RUNNING"
    job.started_at = job.started_at or utcnow()
    job.completed_at = None
    _enqueue_newsletter_tasks(db, job)
    db.commit()
    return dispatch_newsletter_job(db, organization_id, actor_id, job.id)


def update_newsletter_job_state(db: Session, organization_id: str, actor_id: str, job_id: str, next_state: str) -> dict:
    from .service import _stop_observer
    from .runtime import close_local_browser
    from .worker.queue import RedisQueue

    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        raise NewsletterValidationError("Newsletter job not found")
    if next_state == "PAUSED":
        if job.status != "RUNNING":
            raise NewsletterValidationError(f"Cannot pause a {job.status.lower()} newsletter job")
        job.status = "PAUSED"
    elif next_state == "RUNNING":
        if job.status not in {"PAUSED", "DRAFT"}:
            raise NewsletterValidationError(f"Cannot resume a {job.status.lower()} newsletter job")
        if job.status == "DRAFT":
            return start_newsletter_job(db, organization_id, actor_id, job_id)
        job.status = "RUNNING"
        _enqueue_newsletter_tasks(db, job)
    elif next_state == "CANCELLED":
        if job.status in {"COMPLETED", "CANCELLED"}:
            raise NewsletterValidationError(f"Cannot cancel a {job.status.lower()} newsletter job")
        for task in db.scalars(select(NewsletterTask).where(NewsletterTask.job_id == job.id, NewsletterTask.state.in_({"QUEUED", "RETRY_PENDING", "WAITING_CONFIRMATION", "PROCESSING"}))).all():
            if task.browser_session_id:
                close_local_browser(task.browser_session_id)
                _stop_observer(task.browser_session_id)
                session = db.get(BrowserSession, task.browser_session_id)
                if session is not None and session.state != "CLOSED":
                    now = utcnow()
                    session.state = "CLOSED"
                    session.closed_at = now
                    session.last_event_at = now
                    session.login_state = "NEWSLETTER_CANCELLED"
                    session.failure_reason = "Newsletter job cancelled by operator"
                    session.profile.status = "RESERVED"
                    session.profile.lock_state = "UNLOCKED"
                    session.profile.active_session_id = None
                    session.profile.last_closed_at = now
                    session_metadata = dict(session.metadata_json or {})
                    session_metadata["browser_opened"] = False
                    session_metadata["observer_attached"] = False
                    session_metadata["newsletter_result"] = "CANCELLED"
                    session.metadata_json = session_metadata
            _release_newsletter_task_lease(db, task, "job_cancelled")
            task.browser_session_id = None
            task.state = "CANCELLED"
            task.last_error = "Cancelled by operator"
            _record_task_event(db, task, "cancelled", "success", task.last_error)
        RedisQueue().clear(job.id)
        RedisQueue().clear_running(job.id)
        job.status = "CANCELLED"
        job.completed_at = utcnow()
    else:
        raise NewsletterValidationError("Unsupported newsletter job state")
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action=f"newsletter_job.{next_state.lower()}", target_type="newsletter_job", target_id=job.id, result="success", metadata_json={}))
    db.commit()
    if next_state == "RUNNING":
        return dispatch_newsletter_job(db, organization_id, actor_id, job.id)
    return newsletter_job_response(db, job, include_tasks=True)


def retry_newsletter_job(db: Session, organization_id: str, actor_id: str, job_id: str, task_ids: list[str] | None = None) -> dict:
    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        raise NewsletterValidationError("Newsletter job not found")
    query = select(NewsletterTask).where(NewsletterTask.job_id == job.id, NewsletterTask.state.in_({"FAILED", "MANUAL_REVIEW"}))
    if task_ids:
        query = query.where(NewsletterTask.id.in_(list(dict.fromkeys(task_ids))))
    rows = db.scalars(query).all()
    if not rows:
        raise NewsletterValidationError("No failed newsletter tasks are available to retry")
    for task in rows:
        task.state = "QUEUED"
        task.last_error = None
        task.browser_session_id = None
        _record_task_event(db, task, "retry_requested")
    job.status = "RUNNING"
    job.completed_at = None
    _enqueue_newsletter_tasks(db, job)
    db.commit()
    return dispatch_newsletter_job(db, organization_id, actor_id, job.id)


def skip_newsletter_tasks(db: Session, organization_id: str, actor_id: str, job_id: str, task_ids: list[str]) -> dict:
    if not task_ids:
        raise NewsletterValidationError("Select at least one task to skip")
    job = db.scalar(select(NewsletterJob).where(NewsletterJob.organization_id == organization_id, NewsletterJob.id == job_id))
    if not job:
        raise NewsletterValidationError("Newsletter job not found")
    rows = db.scalars(select(NewsletterTask).where(NewsletterTask.job_id == job.id, NewsletterTask.id.in_(list(dict.fromkeys(task_ids))), NewsletterTask.state.in_({"QUEUED", "RETRY_PENDING", "WAITING_CONFIRMATION"}))).all()
    for task in rows:
        task.state = "SKIPPED"
        task.last_error = "Skipped by operator"
        _record_task_event(db, task, "skipped", "success", task.last_error)
    _newsletter_job_complete(db, job)
    db.commit()
    return newsletter_job_response(db, job, include_tasks=True)


def sync_newsletter_task_for_session(db: Session, session: BrowserSession, event: str, detail: str = "") -> None:
    """Update a bulk task from the same observer event used by single tests."""
    metadata = session.metadata_json or {}
    task_id = metadata.get("newsletter_task_id")
    if not task_id:
        return
    task = db.get(NewsletterTask, task_id)
    if not task or (task.browser_session_id and task.browser_session_id != session.id):
        return
    job = db.get(NewsletterJob, task.job_id)
    if not job:
        return
    external_confirmation = bool(metadata.get("newsletter_external_recipient") and metadata.get("newsletter_confirmation_required"))
    mapping = {
        "newsletter.signup.submitted": "WAITING_CONFIRMATION" if job.auto_confirm or external_confirmation else "SUBMITTED",
        "newsletter.signup.failed": "FAILED",
        "newsletter.signup.manual_review": "MANUAL_REVIEW",
        "newsletter.signup.blocked_antibot": "MANUAL_REVIEW",
        "newsletter.signup.skipped": "SKIPPED",
        "newsletter.confirmation.confirmed": "CONFIRMED",
        "newsletter.confirmation.failed": "FAILED",
        "newsletter.confirmation.manual_review": "MANUAL_REVIEW",
        "newsletter.confirmation.skipped": "SKIPPED",
    }
    next_state = mapping.get(event)
    if event in {"newsletter.confirmation.waiting", "newsletter.confirmation.found", "newsletter.confirmation.opened"}:
        next_state = "WAITING_CONFIRMATION"
    if not next_state:
        return
    task.state = next_state
    task.last_error = detail[:500] if next_state in {"FAILED", "MANUAL_REVIEW"} else None
    bulk_task = bool(metadata.get("newsletter_task_id"))
    release_events = {"newsletter.signup.submitted", "newsletter.signup.failed", "newsletter.signup.skipped", "newsletter.confirmation.confirmed", "newsletter.confirmation.failed", "newsletter.confirmation.skipped"}
    if bulk_task:
        release_events.update({"newsletter.signup.manual_review", "newsletter.signup.blocked_antibot", "newsletter.confirmation.manual_review"})
    if event in release_events:
        _release_newsletter_task_lease(db, task)
        task.browser_session_id = None
    _record_task_event(db, task, event, "success" if next_state not in {"FAILED", "MANUAL_REVIEW"} else "attention", detail)
    if next_state == "WAITING_CONFIRMATION" and job.auto_confirm:
        from .worker.queue import RedisQueue
        RedisQueue().requeue(job.id, task.id, task.account_id)
    _newsletter_job_complete(db, job)


def refresh_newsletter_task_heartbeats(db: Session, organization_id: str, worker_id: str) -> int:
    from .service import _locks, get_settings

    now = utcnow()
    count = 0
    for task in db.scalars(select(NewsletterTask).where(NewsletterTask.organization_id == organization_id, NewsletterTask.state == "PROCESSING", NewsletterTask.worker_id == worker_id)).all():
        task.heartbeat_at = now
        if task.lock_key:
            _locks().refresh(task.account_id, task.lock_key, get_settings().worker_lock_ttl * 1000)
        lock_row = db.scalar(select(JobLock).where(JobLock.released == False, JobLock.session_id == task.browser_session_id, JobLock.worker_id == worker_id))
        if lock_row:
            lock_row.heartbeat_at = now
        count += 1
    if count:
        db.commit()
    return count


def recover_stale_newsletter_tasks(db: Session, organization_id: str) -> dict[str, int]:
    from .service import _stop_observer, get_settings
    from .runtime import close_local_browser
    from .worker.queue import RedisQueue

    threshold = utcnow() - __import__("datetime").timedelta(seconds=get_settings().worker_stale_seconds)
    now = utcnow()
    queue = RedisQueue()
    result = {"requeued": 0, "failed": 0, "released": 0}
    rows = db.scalars(
        select(NewsletterTask)
        .outerjoin(BrowserSession, NewsletterTask.browser_session_id == BrowserSession.id)
        .where(
            NewsletterTask.organization_id == organization_id,
            NewsletterTask.state == "PROCESSING",
            or_(NewsletterTask.heartbeat_at < threshold, BrowserSession.id.is_(None), BrowserSession.state == "CLOSED"),
        )
    ).all()
    for task in rows:
        # The observer may have committed a terminal signup result after the
        # stale-task query selected this row but before recovery handles it.
        # Re-read the row so recovery cannot turn a submitted/manual-review
        # task back into QUEUED and launch the same website again.
        try:
            db.expire(task)
            db.refresh(task)
        except Exception:  # noqa: BLE001 - a disappeared row is not recoverable
            continue
        if task.state != "PROCESSING":
            continue
        job = db.get(NewsletterJob, task.job_id)
        if task.browser_session_id:
            session = db.get(BrowserSession, task.browser_session_id)
            if session and session.state in {"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}:
                close_local_browser(session.id)
                _stop_observer(session.id)
                session.state = "CLOSED"
                session.closed_at = now
                session.last_event_at = now
                session.profile.status = "RESERVED"
                session.profile.lock_state = "UNLOCKED"
                session.profile.active_session_id = None
                session.profile.last_closed_at = now
        _release_newsletter_task_lease(db, task, "stale_worker_recovered")
        task.browser_session_id = None
        task.attempts += 1
        if not job or job.status in {"CANCELLED", "COMPLETED", "FAILED"}:
            task.state = "CANCELLED" if job and job.status == "CANCELLED" else "FAILED"
            task.last_error = "Stale task recovered after its job was no longer runnable"
            _record_task_event(db, task, "stale_worker_discarded", "failure", task.last_error)
            result["failed"] += 1
        elif task.attempts >= _newsletter_retry_limit(job):
            task.state = "FAILED"
            task.last_error = "Stale worker exceeded the retry limit"
            _record_task_event(db, task, "stale_worker_failed", "failure", task.last_error)
            result["failed"] += 1
        else:
            task.state = "QUEUED"
            task.last_error = "Stale worker recovered; queued for retry"
            _record_task_event(db, task, "stale_worker_requeued", "retry", task.last_error)
            if job.status == "RUNNING":
                queue.requeue(job.id, task.id, task.account_id)
            result["requeued"] += 1
        result["released"] += 1
        if job:
            _newsletter_job_complete(db, job)
    if rows:
        db.commit()
    return result
