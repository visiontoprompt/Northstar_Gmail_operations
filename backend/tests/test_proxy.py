import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_gmail_ops.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-without-credential-material")
os.environ.setdefault("REDIS_URL", "fakeredis://")

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditEvent, BrowserSession, GmailAccount, OvhProxyGroupLink, ProxyGroup, ProxyInventory
from app.observer import BrowserObserver, _launch_profile_context
from app.proxy import ProxyValidationError, parse_proxy
from app.security import SecretCipher
from app.service import _resolve_proxy_options


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app):
        pass


def test_proxy_parser_supports_anonymous_and_authenticated_formats() -> None:
    anonymous = parse_proxy("203.0.113.10:8080")
    assert anonymous is not None
    assert anonymous.playwright_options() == {"server": "http://203.0.113.10:8080"}

    authenticated = parse_proxy("proxy-user:proxy-pass:203.0.113.10:8080")
    assert authenticated is not None
    assert authenticated.playwright_options() == {
        "server": "http://203.0.113.10:8080",
        "username": "proxy-user",
        "password": "proxy-pass",
    }


@pytest.mark.parametrize("value", ["203.0.113.10", "203.0.113.10:0", "u:p:host", "http://203.0.113.10:8080"])
def test_proxy_parser_rejects_unsupported_values(value: str) -> None:
    with pytest.raises(ProxyValidationError):
        parse_proxy(value)


def test_account_proxy_is_encrypted_and_not_returned_by_api() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        response = client.post(
            "/api/v1/accounts/single",
            json={
                "email": "proxied@example.com",
                "password": "account-password",
                "recovery": "recovery@example.com",
                "2fa": "totp-secret",
                "proxy": "proxy-user:proxy-pass:203.0.113.10:8080",
                "group_id": group_id,
            },
        )
        assert response.status_code == 201
        body = response.text
        assert "proxy-pass" not in body
        assert response.json()["proxy"]["status"] == "ASSIGNED"
        assert response.json()["proxy"]["label"] == "203.0.113.10:8080"

        account_id = response.json()["id"]
        db = SessionLocal()
        try:
            secret = db.query(GmailAccount).filter_by(id=account_id).one().secret
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["proxy"] == {"host": "203.0.113.10", "port": 8080, "username": "proxy-user", "password": "proxy-pass"}
            assert "proxy-pass" not in secret.encrypted_payload
        finally:
            db.close()


def test_group_distribution_balances_healthy_proxies_and_rejects_short_capacity() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account_ids = []
        for index in range(4):
            response = client.post("/api/v1/accounts/single", json={"email": f"balanced-{index}@example.com", "password": "account-password", "group_id": group_id})
            assert response.status_code == 201, response.text
            account_ids.append(response.json()["id"])
        imported = client.post("/api/v1/proxies/import", json={"lines": "203.0.113.10:8080|P1\n203.0.113.11:8080|P2"})
        assert imported.status_code == 201, imported.text
        proxy_ids = [row["proxy_id"] for row in imported.json()["rows"]]
        with SessionLocal() as db:
            for proxy_id in proxy_ids:
                db.get(ProxyInventory, proxy_id).test_status = "HEALTHY"
            db.commit()
        payload = {"group_id": group_id, "proxy_ids": proxy_ids, "accounts_per_proxy": 2}
        assigned = client.post("/api/v1/proxies/distribute-group", json=payload)
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["changed_count"] == 4
        with SessionLocal() as db:
            counts = [sum(db.get(GmailAccount, account_id).proxy_id == proxy_id for account_id in account_ids) for proxy_id in proxy_ids]
            assert counts == [2, 2]
        repeated = client.post("/api/v1/proxies/distribute-group", json=payload)
        assert repeated.status_code == 200
        assert repeated.json()["changed_count"] == 0
        too_small = client.post("/api/v1/proxies/distribute-group", json={**payload, "accounts_per_proxy": 1})
        assert too_small.status_code == 422
        with SessionLocal() as db:
            assert [sum(db.get(GmailAccount, account_id).proxy_id == proxy_id for account_id in account_ids) for proxy_id in proxy_ids] == [2, 2]
            db.get(GmailAccount, account_ids[0]).browser_profile.lock_state = "LOCKED"
            db.commit()
        locked = client.post("/api/v1/proxies/distribute-group", json=payload)
        assert locked.status_code == 422
        assert "active sessions" in locked.json()["detail"].lower()


def test_group_distribution_equal_mode_needs_no_capacity_limit() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        for index in range(5):
            assert client.post("/api/v1/accounts/single", json={"email": f"equal-{index}@example.com", "password": "account-password", "group_id": group_id}).status_code == 201
        imported = client.post("/api/v1/proxies/import", json={"lines": "203.0.113.21:8080|E1\n203.0.113.22:8080|E2"})
        proxy_ids = [row["proxy_id"] for row in imported.json()["rows"]]
        with SessionLocal() as db:
            for proxy_id in proxy_ids:
                db.get(ProxyInventory, proxy_id).test_status = "HEALTHY"
            db.commit()
        result = client.post("/api/v1/proxies/distribute-group", json={"group_id": group_id, "proxy_ids": proxy_ids, "mode": "equal"})
        assert result.status_code == 200, result.text
        assert result.json()["account_count"] == 5
        assert sorted(result.json()["distribution"].values()) == [2, 3]
        assert result.json()["accounts_per_proxy"] is None


def test_proxy_endpoints_can_be_grouped_and_filtered_in_inventory() -> None:
    with TestClient(app) as client:
        imported = client.post("/api/v1/proxies/import", json={"lines": "203.0.113.41:8080|East 1\n203.0.113.42:8080|East 2"})
        assert imported.status_code == 201, imported.text
        proxy_ids = [row["proxy_id"] for row in imported.json()["rows"]]
        grouped = client.post("/api/v1/proxies/group", json={"proxy_ids": proxy_ids, "group_name": "US East"})
        assert grouped.status_code == 200, grouped.text
        inventory = client.get("/api/v1/proxies").json()
        assert {item["proxy_group"] for item in inventory if item["id"] in proxy_ids} == {"US East"}
        ungrouped = client.post("/api/v1/proxies/group", json={"proxy_ids": [proxy_ids[0]], "group_name": None})
        assert ungrouped.status_code == 200, ungrouped.text
        inventory = client.get("/api/v1/proxies").json()
        groups = {item["id"]: item["proxy_group"] for item in inventory if item["id"] in proxy_ids}
        assert groups == {proxy_ids[0]: None, proxy_ids[1]: "US East"}


def test_saved_profile_reconciles_stale_required_login_status() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "reconciled@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id},
        ).json()
        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            row.login_status = "REQUIRED"
            row.profile_status = "SAVED"
            row.browser_profile.authenticated_at = datetime.now(timezone.utc)
            row.browser_profile.active_session_id = None
            db.commit()
        finally:
            db.close()
        assert client.get(f"/api/v1/accounts/{account['id']}").json()["login_status"] == "AUTHENTICATED"


def test_proxy_metadata_edit_keeps_existing_connection_when_proxy_is_omitted() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "proxy-preserve@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "proxy": "proxy-user:proxy-pass:203.0.113.10:8080", "group_id": group_id},
        ).json()
        updated = client.patch(f"/api/v1/accounts/{account['id']}", json={"proxy_label": "Primary egress"})
        assert updated.status_code == 200
        assert updated.json()["proxy"]["label"] == "Primary egress"

        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            session = BrowserSession(id="session_proxy_preserve", organization_id=row.organization_id, account_id=row.id, profile_id=row.browser_profile.id, metadata_json={})
            db.add(session)
            db.commit()
            assert _resolve_proxy_options(session.id) == {"server": "http://203.0.113.10:8080", "username": "proxy-user", "password": "proxy-pass"}
        finally:
            db.close()


def test_observer_passes_account_proxy_to_playwright(tmp_path) -> None:
    playwright = MagicMock()
    expected = {"server": "http://203.0.113.10:8080", "username": "proxy-user", "password": "proxy-pass"}
    observer = BrowserObserver("s1", "proxied@example.com__org", 0, profile_dir=str(tmp_path), executable="fake-browser", headless=True, resolve_proxy=lambda _session_id: expected)

    _launch_profile_context(playwright, observer)

    kwargs = playwright.chromium.launch_persistent_context.call_args.kwargs
    assert kwargs["proxy"] == expected


def test_observer_keeps_normal_chrome_window_for_in_context_navigation(tmp_path) -> None:
    playwright = MagicMock()
    start_url = "https://myaccount.google.com/account-enhanced-safe-browsing"
    observer = BrowserObserver("s1", "account@example.com__org", 0, profile_dir=str(tmp_path), executable="fake-browser", headless=True, start_url=start_url)

    _launch_profile_context(playwright, observer)

    kwargs = playwright.chromium.launch_persistent_context.call_args.kwargs
    assert all(not argument.startswith("--app=") for argument in kwargs["args"])


def test_external_newsletter_context_can_launch_headless_and_incognito(tmp_path) -> None:
    playwright = MagicMock()
    observer = BrowserObserver(
        "s-external",
        "external-profile",
        0,
        profile_dir=str(tmp_path),
        executable="fake-browser",
        headless=True,
        newsletter_config={"ephemeral_browser": True, "headless_browser": True},
        purpose="newsletter",
    )

    _launch_profile_context(playwright, observer)

    kwargs = playwright.chromium.launch_persistent_context.call_args.kwargs
    assert kwargs["headless"] is True
    assert "--incognito" in kwargs["args"]


def test_proxy_inventory_can_reassign_edit_and_remove_accounts() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = [client.post("/api/v1/accounts/single", json={"email": f"proxy-{index}@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json() for index in range(3)]
        assigned = client.post("/api/v1/proxies/assignments", json={"account_ids": [accounts[0]["id"], accounts[1]["id"]], "proxy": "proxy-user:proxy-pass:203.0.113.10:8080", "label": "Primary egress", "region": "New York"})
        assert assigned.status_code == 200
        proxy_id = assigned.json()["proxy_id"]
        assert client.get("/api/v1/proxies").json()[0]["account_count"] == 2

        reassigned = client.post("/api/v1/proxies/assignments", json={"proxy_id": proxy_id, "account_ids": [accounts[2]["id"]]})
        assert reassigned.status_code == 200
        assert client.get("/api/v1/proxies").json()[0]["account_count"] == 3
        edited = client.patch(f"/api/v1/proxies/{proxy_id}", json={"label": "Updated egress", "region": "Paris"})
        assert edited.status_code == 200
        assert client.get("/api/v1/proxies").json()[0]["label"] == "Updated egress"

        removed = client.request("DELETE", f"/api/v1/proxies/{proxy_id}", json={"confirmation": "REMOVE"})
        assert removed.status_code == 200
        assert client.get("/api/v1/proxies").json() == []


def test_bulk_proxy_import_stores_unassigned_inventory_without_exposing_credentials() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/proxies/import",
            json={"lines": "proxy-user:proxy-pass:203.0.113.40:8080 | US-NY-0040 | New York, US"},
        )

        assert response.status_code == 201
        assert "proxy-pass" not in response.text
        result = response.json()
        assert result["imported"] == 1
        assert result["duplicates"] == 0
        assert result["invalid"] == 0
        assert result["rows"] == [{"line_number": 1, "status": "IMPORTED", "message": "Stored as ungrouped inventory", "proxy_id": result["rows"][0]["proxy_id"]}]

        proxies = client.get("/api/v1/proxies").json()
        assert len(proxies) == 1
        assert proxies[0]["label"] == "US-NY-0040"
        assert proxies[0]["region"] == "New York, US"
        assert proxies[0]["account_count"] == 0
        assert "proxy-pass" not in str(proxies)

        db = SessionLocal()
        try:
            inventory = db.query(ProxyInventory).one()
            audit = db.query(AuditEvent).filter_by(action="proxy.imported").one()
            assert "proxy-pass" not in (inventory.encrypted_payload or "")
            assert audit.metadata_json == {"proxy_id": inventory.id, "proxy_authentication": "authenticated", "source": "bulk_import", "proxy_group": None}
        finally:
            db.close()


def test_bulk_proxy_import_assigns_all_new_endpoints_to_selected_group() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/proxies/import",
            json={"lines": "198.51.100.81:8080 | East 1\n198.51.100.82:8080 | East 2", "group_name": "US East"},
        )

        assert response.status_code == 201
        assert response.json()["imported"] == 2
        proxies = client.get("/api/v1/proxies").json()
        assert {proxy["proxy_group"] for proxy in proxies} == {"US East"}
        groups = client.get("/api/v1/proxies/groups").json()
        assert next(group for group in groups if group["name"] == "US East")["proxy_count"] == 2


def test_proxy_group_rename_updates_endpoints_and_ovh_links_without_changing_account_assignment() -> None:
    with TestClient(app) as client:
        created = client.post("/api/v1/proxies/groups", json={"name": "East pool"})
        assert created.status_code == 201, created.text
        group = created.json()
        imported = client.post("/api/v1/proxies/import", json={"lines": "203.0.113.87:8080|East endpoint", "group_name": "East pool"})
        assert imported.status_code == 201, imported.text
        proxy_id = imported.json()["rows"][0]["proxy_id"]
        account_group = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "group-rename@example.com", "password": "secret", "group_id": account_group}).json()
        assigned = client.post("/api/v1/proxies/assignments", json={"proxy_id": proxy_id, "account_ids": [account["id"]]})
        assert assigned.status_code == 200, assigned.text
        with SessionLocal() as db:
            organization_id = db.get(ProxyInventory, proxy_id).organization_id
            db.add(OvhProxyGroupLink(id="ovh-group-rename-test", organization_id=organization_id, proxy_group="East pool", resource_type="instance", service_name="test-instance", instance_id="test-instance"))
            db.commit()

        renamed = client.patch(f"/api/v1/proxies/groups/{group['id']}", json={"name": "East production"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["proxy_count"] == 1
        assert any(item["id"] == group["id"] and item["name"] == "East production" for item in client.get("/api/v1/proxies/groups").json())
        assert next(item for item in client.get("/api/v1/proxies").json() if item["id"] == proxy_id)["proxy_group"] == "East production"
        with SessionLocal() as db:
            assert db.query(OvhProxyGroupLink).filter_by(id="ovh-group-rename-test").one().proxy_group == "East production"
            assert db.get(GmailAccount, account["id"]).proxy_id == proxy_id


def test_deleting_proxy_group_ungroups_only_and_preserves_endpoint_and_account_assignment() -> None:
    with TestClient(app) as client:
        group = client.post("/api/v1/proxies/groups", json={"name": "Temporary pool"}).json()
        imported = client.post("/api/v1/proxies/import", json={"lines": "203.0.113.88:8080|Keep endpoint", "group_name": "Temporary pool"})
        proxy_id = imported.json()["rows"][0]["proxy_id"]
        account_group = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "group-delete@example.com", "password": "secret", "group_id": account_group}).json()
        assert client.post("/api/v1/proxies/assignments", json={"proxy_id": proxy_id, "account_ids": [account["id"]]}).status_code == 200
        with SessionLocal() as db:
            organization_id = db.get(ProxyInventory, proxy_id).organization_id
            db.add(OvhProxyGroupLink(id="ovh-group-delete-test", organization_id=organization_id, proxy_group="Temporary pool", resource_type="instance", service_name="test-instance", instance_id="test-instance"))
            db.commit()

        deleted = client.delete(f"/api/v1/proxies/groups/{group['id']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["ungrouped_count"] == 1
        assert not any(item["name"] == "Temporary pool" for item in client.get("/api/v1/proxies/groups").json())
        proxy = next(item for item in client.get("/api/v1/proxies").json() if item["id"] == proxy_id)
        assert proxy["proxy_group"] is None
        assert proxy["account_count"] == 1
        with SessionLocal() as db:
            assert db.get(GmailAccount, account["id"]).proxy_id == proxy_id
            assert db.get(ProxyInventory, proxy_id) is not None
            assert db.get(OvhProxyGroupLink, "ovh-group-delete-test") is None


def test_legacy_proxy_group_can_be_renamed_and_duplicate_names_are_rejected() -> None:
    with TestClient(app) as client:
        imported = client.post("/api/v1/proxies/import", json={"lines": "203.0.113.89:8080|Legacy endpoint"})
        proxy_id = imported.json()["rows"][0]["proxy_id"]
        with SessionLocal() as db:
            db.get(ProxyInventory, proxy_id).proxy_group = "Legacy pool"
            db.commit()
        groups = client.get("/api/v1/proxies/groups").json()
        legacy = next(item for item in groups if item["name"] == "Legacy pool")
        renamed = client.patch(f"/api/v1/proxies/groups/{legacy['id']}", json={"name": "Legacy renamed"})
        assert renamed.status_code == 200, renamed.text
        duplicate = client.post("/api/v1/proxies/groups", json={"name": "Existing"})
        assert duplicate.status_code == 201
        collision = client.patch(f"/api/v1/proxies/groups/{renamed.json()['id']}", json={"name": "Existing"})
        assert collision.status_code == 422
        assert next(item for item in client.get("/api/v1/proxies").json() if item["id"] == proxy_id)["proxy_group"] == "Legacy renamed"


def test_bulk_proxy_import_reports_invalid_and_duplicate_rows_without_overwriting_endpoint() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/proxies/import",
            json={"lines": "198.51.100.44:8080 | First endpoint\nnot-a-proxy\n198.51.100.44:8080 | Replacement attempt"},
        )

        assert response.status_code == 201
        assert response.json()["imported"] == 1
        assert response.json()["duplicates"] == 1
        assert response.json()["invalid"] == 1
        assert [row["status"] for row in response.json()["rows"]] == ["IMPORTED", "INVALID", "DUPLICATE"]
        assert client.get("/api/v1/proxies").json()[0]["label"] == "First endpoint"


def test_unassign_keeps_proxy_inventory_available_for_reassignment() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = [client.post("/api/v1/accounts/single", json={"email": f"proxy-retain-{index}@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json() for index in range(3)]
        assigned = client.post("/api/v1/proxies/assignments", json={"account_ids": [accounts[0]["id"]], "proxy": "proxy-user:proxy-pass:198.51.100.44:8080"})
        assert assigned.status_code == 200
        proxy_id = assigned.json()["proxy_id"]

        cleared = client.request("DELETE", f"/api/v1/proxies/{proxy_id}/assignments", json={"account_ids": [accounts[0]["id"]]})
        assert cleared.status_code == 200
        retained = client.get("/api/v1/proxies").json()
        assert retained[0]["id"] == proxy_id
        assert retained[0]["account_count"] == 0

        reassigned = client.post("/api/v1/proxies/assignments", json={"proxy_id": proxy_id, "account_ids": [accounts[1]["id"]]})
        assert reassigned.status_code == 200
        assert reassigned.json()["proxy_id"] == proxy_id
        assert client.get("/api/v1/proxies").json()[0]["account_count"] == 1


def test_group_proxy_assignment_replaces_only_selected_group_atomically() -> None:
    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        group_one, group_two = groups[0]["id"], groups[1]["id"]
        first = client.post("/api/v1/accounts/single", json={"email": "proxy-group-one@example.com", "password": "secret", "group_id": group_one}).json()
        second = client.post("/api/v1/accounts/single", json={"email": "proxy-group-two@example.com", "password": "secret", "group_id": group_two}).json()
        imported = client.post("/api/v1/proxies/import", json={"lines": "198.51.100.121:8080 | Shared proxy"})
        assert imported.status_code == 201, imported.text
        proxy_id = imported.json()["rows"][0]["proxy_id"]

        first_assignment = client.post(f"/api/v1/proxies/{proxy_id}/group-assignments", json={"group_id": group_one, "account_ids": [first["id"]]})
        second_assignment = client.post(f"/api/v1/proxies/{proxy_id}/group-assignments", json={"group_id": group_two, "account_ids": [second["id"]]})
        assert first_assignment.status_code == 200, first_assignment.text
        assert second_assignment.status_code == 200, second_assignment.text

        # Clearing group one must preserve the same shared proxy in group two.
        cleared = client.post(f"/api/v1/proxies/{proxy_id}/group-assignments", json={"group_id": group_one, "account_ids": []})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["unassigned_count"] == 1
        with SessionLocal() as db:
            assert db.get(GmailAccount, first["id"]).proxy_id is None
            assert db.get(GmailAccount, second["id"]).proxy_id == proxy_id


def test_group_proxy_assignment_recovers_legacy_proxy_without_inventory_row() -> None:
    """Older account records remain assignable even before inventory backfill."""
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        first = client.post("/api/v1/accounts/single", json={"email": "proxy-legacy-source@example.com", "password": "secret", "group_id": group_id}).json()
        second = client.post("/api/v1/accounts/single", json={"email": "proxy-legacy-target@example.com", "password": "secret", "group_id": group_id}).json()
        assigned = client.post(
            "/api/v1/proxies/assignments",
            json={"account_ids": [first["id"]], "proxy": "legacy-user:legacy-pass:198.51.100.123:8080"},
        )
        assert assigned.status_code == 200, assigned.text
        proxy_id = assigned.json()["proxy_id"]

        # Simulate a proxy assignment from before the inventory table existed.
        with SessionLocal() as db:
            inventory = db.get(ProxyInventory, proxy_id)
            assert inventory is not None
            db.delete(inventory)
            db.commit()

        response = client.post(
            f"/api/v1/proxies/{proxy_id}/group-assignments",
            json={"group_id": group_id, "account_ids": [second["id"]]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["assigned_count"] == 1
        with SessionLocal() as db:
            assert db.get(GmailAccount, second["id"]).proxy_id == proxy_id
            assert db.get(ProxyInventory, proxy_id) is not None


def test_proxy_assignment_postgres_lock_targets_accounts_not_outer_joined_tables() -> None:
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import joinedload

    statement = (
        select(GmailAccount)
        .options(joinedload(GmailAccount.browser_profile))
        .with_for_update(of=GmailAccount)
    )
    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert f"FOR UPDATE OF {GmailAccount.__tablename__}" in compiled


def test_scoped_operator_can_assign_unassigned_inventory_only_inside_accessible_group() -> None:
    from unittest.mock import patch

    from app.auth import CurrentUser

    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        allowed_group, blocked_group = groups[0]["id"], groups[1]["id"]
        allowed = client.post("/api/v1/accounts/single", json={"email": "proxy-group-scope-allowed@example.com", "password": "secret", "group_id": allowed_group}).json()
        blocked = client.post("/api/v1/accounts/single", json={"email": "proxy-group-scope-blocked@example.com", "password": "secret", "group_id": blocked_group}).json()
        imported = client.post("/api/v1/proxies/import", json={"lines": "198.51.100.122:8080 | New inventory"}).json()
        proxy_id = imported["rows"][0]["proxy_id"]

    operator = CurrentUser("operator_group_scoped", "org_demo", "Scoped operator", "operator@example.com", "ACCOUNT_OPERATOR", {allowed_group: "OPERATOR"})
    with patch("app.main.authenticate_request", return_value=operator), patch("app.auth.authenticate_request", return_value=operator):
        with TestClient(app) as client:
            allowed_result = client.post(f"/api/v1/proxies/{proxy_id}/group-assignments", json={"group_id": allowed_group, "account_ids": [allowed["id"]]})
            assert allowed_result.status_code == 200, allowed_result.text
            denied = client.post(f"/api/v1/proxies/{proxy_id}/group-assignments", json={"group_id": blocked_group, "account_ids": [blocked["id"]]})
            assert denied.status_code == 404


def test_group_operator_can_assign_proxy_only_inside_their_groups() -> None:
    from unittest.mock import patch

    from app.auth import CurrentUser

    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        allowed_group, blocked_group = groups[0]["id"], groups[1]["id"]
        allowed = client.post("/api/v1/accounts/single", json={"email": "proxy-scope-allowed@example.com", "password": "secret", "recovery": "recover@example.com", "2fa": "totp", "group_id": allowed_group}).json()
        blocked = client.post("/api/v1/accounts/single", json={"email": "proxy-scope-blocked@example.com", "password": "secret", "recovery": "recover@example.com", "2fa": "totp", "group_id": blocked_group}).json()
        imported = client.post("/api/v1/proxies/import", json={"lines": "proxy-user:proxy-pass:203.0.113.88:8080 | scoped"}).json()
        proxy_id = imported["rows"][0]["proxy_id"]

    operator = CurrentUser("operator_scoped", "org_demo", "Scoped operator", "operator@example.com", "ACCOUNT_OPERATOR", {allowed_group: "OPERATOR"})
    with patch("app.main.authenticate_request", return_value=operator), patch("app.auth.authenticate_request", return_value=operator):
        with TestClient(app) as client:
            response = client.post("/api/v1/proxies/assignments", json={"proxy_id": proxy_id, "account_ids": [allowed["id"]]})
            assert response.status_code == 200, response.text
            assert response.json()["updated_count"] == 1
            denied = client.post("/api/v1/proxies/assignments", json={"proxy_id": proxy_id, "account_ids": [blocked["id"]]})
            assert denied.status_code == 404


def test_observer_does_not_fall_back_to_direct_when_proxy_resolution_fails(tmp_path) -> None:
    playwright = MagicMock()
    observer = BrowserObserver("s1", "account@example.com__org", 0, profile_dir=str(tmp_path), executable="fake-browser", headless=True, resolve_proxy=lambda _session_id: (_ for _ in ()).throw(RuntimeError("proxy unavailable")))

    assert _launch_profile_context(playwright, observer) is None
    playwright.chromium.launch_persistent_context.assert_not_called()
