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
from app.models import BrowserSession, GmailAccount
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
