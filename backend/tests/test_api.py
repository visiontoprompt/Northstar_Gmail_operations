import os

os.environ["DATABASE_URL"] = "sqlite:///./data/test_gmail_ops.db"
os.environ["SECRET_KEY"] = "test-secret-key-without-credential-material"
os.environ["REDIS_URL"] = "fakeredis://"

from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.main import settings
from app.security import SecretCipher
from app.worker import redis as worker_redis


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    worker_redis.reset_redis()
    worker_redis.flush_redis()
    with TestClient(app):
        pass


def test_employees_endpoint_lists_workspace_employees() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/employees")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_production_api_requires_bearer_token() -> None:
    from unittest.mock import MagicMock, patch

    original_environment = settings.environment
    original_token = settings.admin_api_token
    settings.environment = "production"
    settings.admin_api_token = "test-admin-token"
    try:
        with patch("app.main.verify_production_schema"):
            with TestClient(app) as client:
                assert client.get("/api/v1/health").status_code == 200
                assert client.get("/api/v1/accounts").status_code == 401
                response = client.get("/api/v1/accounts", headers={"Authorization": "Bearer test-admin-token"})
                assert response.status_code == 200
                assert {"ALL", "READY_FOR_LOGIN", "SETUP_REQUIRED", "AUTHENTICATED", "NEEDS_ATTENTION", "NONEXISTENT", "VERIFICATION_REQUIRED", "NEVER_LOGGED_IN"} <= response.json()["status_counts"].keys()
    finally:
        settings.environment = original_environment
        settings.admin_api_token = original_token


def test_browser_desktop_is_explicitly_gated_and_enabled_only_when_configured() -> None:
    original_enabled = settings.browser_desktop_enabled
    try:
        settings.browser_desktop_enabled = False
        with TestClient(app) as client:
            assert client.get("/api/v1/desktop/status").json() == {"enabled": False}
            assert client.get("/api/v1/desktop/authorize").status_code == 404
        settings.browser_desktop_enabled = True
        with TestClient(app) as client:
            assert client.get("/api/v1/desktop/status").json() == {"enabled": True}
            assert client.get("/api/v1/desktop/authorize").status_code == 204
    finally:
        settings.browser_desktop_enabled = original_enabled


def test_browser_desktop_rejects_non_admins() -> None:
    from unittest.mock import patch

    from app.auth import CurrentUser

    original_enabled = settings.browser_desktop_enabled
    settings.browser_desktop_enabled = True
    scoped_user = CurrentUser("employee_scoped", settings.demo_org_id, "Scoped operator", "operator@example.com", "OPERATOR", {})
    try:
        with TestClient(app) as client:
            group_id = client.get("/api/v1/groups").json()[0]["id"]
            account = client.post("/api/v1/accounts/single", json={"email": "restricted-focus@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
            session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        with patch("app.main.authenticate_request", return_value=scoped_user), patch("app.auth.authenticate_request", return_value=scoped_user):
            with TestClient(app) as client:
                assert client.get("/api/v1/desktop/status").status_code == 403
                assert client.get("/api/v1/desktop/authorize").status_code == 403
                assert client.get("/api/v1/desktop/runtime").status_code == 403
                assert client.post(f"/api/v1/sessions/{session['id']}/focus").status_code in {403, 404}
    finally:
        settings.browser_desktop_enabled = original_enabled


def test_account_operator_can_access_desktop_and_infrastructure_but_not_admin_pages() -> None:
    from unittest.mock import MagicMock, patch

    from app.auth import CurrentUser

    original_enabled = settings.browser_desktop_enabled
    settings.browser_desktop_enabled = True
    try:
        with TestClient(app) as client:
            group_id = client.get("/api/v1/groups").json()[0]["id"]
        operator = CurrentUser("operator_scoped", settings.demo_org_id, "Scoped operator", "operator@example.com", "ACCOUNT_OPERATOR", {group_id: "OPERATOR"})
        ovh_client = MagicMock()
        ovh_client.__enter__.return_value.list_resources.return_value = {"items": [], "errors": []}
        with patch("app.main.authenticate_request", return_value=operator), patch("app.auth.authenticate_request", return_value=operator), patch("app.main.configured_client", return_value=ovh_client):
            with TestClient(app) as client:
                assert client.get("/api/v1/desktop/status").json() == {"enabled": True}
                assert client.get("/api/v1/desktop/authorize").status_code == 204
                runtime = client.get("/api/v1/desktop/runtime")
                assert runtime.status_code == 200
                assert set(runtime.json()) == {"default_login_concurrency", "max_active_profiles"}
                assert client.get("/api/v1/infrastructure/ovh").status_code == 200
                assert client.get("/api/v1/infrastructure/godaddy/accounts").status_code == 200
                assert client.get("/api/v1/employees").status_code == 403
                assert client.get("/api/v1/settings").status_code == 403
    finally:
        settings.browser_desktop_enabled = original_enabled


def test_bulk_preview_masks_secret_material() -> None:
    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        group_id = groups[0]["id"]
        raw = "agent01@gmail.com:S3cretPassword:recovery@example.com:TOTPSECRET"
        response = client.post("/api/v1/accounts/bulk/preview", json={"raw_lines": raw, "group_id": group_id})
        assert response.status_code == 200
        payload = response.json()
        assert payload["valid_rows"] == 1
        serialized = response.text
        assert "S3cretPassword" not in serialized
        assert "TOTPSECRET" not in serialized
        assert "recovery@example.com" not in serialized
        assert payload["rows"][0]["recovery_masked"].startswith("r")


def test_bulk_preview_accepts_optional_recovery_and_two_fa() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        raw = "minimal@example.com:secret\nrecovery@example.com:secret:backup@example.com\nfull@example.com:secret:backup@example.com:TOTPSECRET"
        response = client.post("/api/v1/accounts/bulk/preview", json={"raw_lines": raw, "group_id": group_id})
        assert response.status_code == 200
        payload = response.json()
        assert payload["valid_rows"] == 3
        assert payload["invalid_rows"] == 0
        assert payload["rows"][0]["recovery_masked"] is None
        assert payload["rows"][0]["two_fa_configured"] is False
        assert payload["rows"][1]["recovery_masked"].startswith("b")
        assert payload["rows"][1]["two_fa_configured"] is False
        assert payload["rows"][2]["two_fa_configured"] is True


def test_single_account_accepts_only_email_and_password() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        response = client.post("/api/v1/accounts/single", json={"email": "minimal-single@example.com", "password": "secret", "group_id": group_id})
        assert response.status_code == 201
        body = response.json()
        assert body["recovery_masked"] == "Not provided"
        assert body["two_fa_configured"] is False


def test_account_tags_and_new_registry_filters() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        created = client.post("/api/v1/accounts/single", json={"email": "tagged@example.com", "password": "secret", "group_id": group_id})
        assert created.status_code == 201
        account_id = created.json()["id"]
        updated = client.patch(f"/api/v1/accounts/{account_id}/tags", json={"tags": [" review ", "priority", "review"]})
        assert updated.status_code == 200
        assert updated.json()["tags"] == ["review", "priority"]
        never_logged = client.get("/api/v1/accounts", params={"status": "NEVER_LOGGED_IN"}).json()
        assert [item["id"] for item in never_logged["items"]] == [account_id]
        assert never_logged["status_counts"]["NEVER_LOGGED_IN"] == 1


def test_account_preferences_upload_save_and_read_without_exposing_file_data() -> None:
    import tempfile

    original_profile_root = settings.profile_root
    with tempfile.TemporaryDirectory() as profile_root:
        settings.profile_root = profile_root
        try:
            with TestClient(app) as client:
                group_id = client.get("/api/v1/groups").json()[0]["id"]
                account = client.post("/api/v1/accounts/single", json={"email": "preferences@example.com", "password": "secret", "group_id": group_id}).json()
                initial = client.get(f"/api/v1/accounts/{account['id']}/preferences")
                assert initial.status_code == 200
                assert initial.json()["gmail_theme"] == "default"
                uploaded = client.post(f"/api/v1/accounts/{account['id']}/preferences/avatar", files={"file": ("profile.png", b"fake-png", "image/png")})
                assert uploaded.status_code == 200
                assert uploaded.json()["avatar_available"] is True
                saved = client.put(f"/api/v1/accounts/{account['id']}/preferences", json={"gmail_theme": "dark", "avatar_filename": uploaded.json()["avatar_filename"]})
                assert saved.status_code == 200
                assert saved.json()["gmail_theme"] == "dark"
                assert "fake-png" not in saved.text
                current = client.get(f"/api/v1/accounts/{account['id']}/preferences").json()
                assert current["gmail_theme"] == "dark"
                assert current["avatar_available"] is True
        finally:
            settings.profile_root = original_profile_root


def test_bulk_import_persists_metadata_separately_from_encrypted_secret() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        raw = "agent02@gmail.com:S3cretPassword:recovery@example.com:TOTPSECRET"
        response = client.post("/api/v1/accounts/bulk/import", json={"raw_lines": raw, "group_id": group_id, "idempotency_key": "import-test-0001"})
        assert response.status_code == 201
        account_id = response.json()["account_ids"][0]
        detail = client.get(f"/api/v1/accounts/{account_id}")
        assert detail.status_code == 200
        body = detail.text
        assert "S3cretPassword" not in body
        assert "TOTPSECRET" not in body
        assert body.find("READY_FOR_LOGIN") >= 0
        assert body.find("STORED_ENCRYPTED") >= 0
        account_list = client.get("/api/v1/accounts").json()
        assert account_list["total"] == 1
        assert account_list["items"][0]["profile"]["status"] == "RESERVED"


def test_infrastructure_inventory_endpoints_are_safe_and_account_scoped() -> None:
    from app.main import settings as app_settings
    import tempfile

    original_profile_root = app_settings.profile_root
    with tempfile.TemporaryDirectory() as profile_root:
        app_settings.profile_root = profile_root
        try:
            with TestClient(app) as client:
                group_id = client.get("/api/v1/groups").json()[0]["id"]
                account = client.post("/api/v1/accounts/single", json={"email": "inventory@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "proxy": "proxy-user:proxy-pass:198.51.100.12:8080", "group_id": group_id}).json()
                second_group_id = client.get("/api/v1/groups").json()[1]["id"]
                second_account = client.post("/api/v1/accounts/single", json={"email": "inventory-two@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "proxy": "proxy-user:proxy-pass:198.51.100.12:8080", "group_id": second_group_id}).json()

                # A reserved onboarding row is not a real profile until its
                # persistent user-data directory exists.
                assert client.get("/api/v1/browser-profiles").json() == []
                from pathlib import Path
                (Path(profile_root) / account["profile"]["id"]).mkdir(parents=True)
                profiles = client.get("/api/v1/browser-profiles").json()
                assert profiles[0]["account_id"] == account["id"]
                assert profiles[0]["status"] == "RESERVED"
                assert "proxy-pass" not in client.get("/api/v1/browser-profiles").text

                proxies = client.get("/api/v1/proxies").json()
                assert len(proxies) == 1
                assert proxies[0]["account_count"] == 2
                assert {reference["id"] for reference in proxies[0]["account_refs"]} == {account["id"], second_account["id"]}
                scoped_proxies = client.get(f"/api/v1/proxies?group_id={group_id}").json()
                assert scoped_proxies[0]["account_count"] == 1
                assert scoped_proxies[0]["account_refs"] == [{"id": account["id"], "email": "inventory@example.com"}]
                assert "proxy-user" not in client.get("/api/v1/proxies").text
                assert "proxy-pass" not in client.get("/api/v1/proxies").text
        finally:
            app_settings.profile_root = original_profile_root


def test_latest_sessions_endpoint_returns_one_record_per_account_and_supports_group_scope() -> None:
    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        account = client.post("/api/v1/accounts/single", json={"email": "latest@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": groups[0]["id"]}).json()
        first = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        client.post(f"/api/v1/sessions/{first['id']}/close")
        second = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()

        latest = client.get("/api/v1/sessions?latest_per_account=true&limit=100").json()
        matching = [session for session in latest if session["account_id"] == account["id"]]
        assert len(matching) == 1
        assert matching[0]["id"] == second["id"]
        assert matching[0]["action"] == "Login"
        assert matching[0]["operation_status"] == "IN_PROGRESS"
        history = client.get(f"/api/v1/sessions?latest_per_account=false&purpose=login&limit=100").json()
        account_history = [session for session in history if session["account_id"] == account["id"]]
        assert len(account_history) == 2
        assert {session["operation_status"] for session in account_history} == {"NOT_FINISHED", "IN_PROGRESS"}
        unfinished = client.get("/api/v1/sessions?latest_per_account=false&outcome=NOT_FINISHED&limit=100").json()
        assert any(session["id"] == first["id"] for session in unfinished)
        scoped = client.get(f"/api/v1/sessions?latest_per_account=true&group_id={groups[0]['id']}").json()
        assert any(session["account_id"] == account["id"] for session in scoped)


def test_session_history_delete_removes_closed_records_and_preserves_active_sessions() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "history-delete@example.com", "password": "secret", "group_id": group_id}).json()
        closed = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        client.post(f"/api/v1/sessions/{closed['id']}/close")
        active = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()

        response = client.delete("/api/v1/sessions/history")

        assert response.status_code == 200
        assert response.json() == {"deleted_count": 1, "active_preserved_count": 1}
        assert client.get(f"/api/v1/sessions/{closed['id']}").status_code == 404
        assert client.get(f"/api/v1/sessions/{active['id']}").status_code == 200


def test_deduplicates_case_insensitively_and_reports_invalid_rows() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        raw = "agent03@gmail.com:one:recovery@example.com:totp\nAGENT03@GMAIL.COM:two:recovery@example.com:totp\nnot-an-email:three:recovery@example.com:totp\nmalformed"
        preview = client.post("/api/v1/accounts/bulk/preview", json={"raw_lines": raw, "group_id": group_id}).json()
        assert preview["valid_rows"] == 1
        assert preview["duplicate_rows"] == 1
        assert preview["invalid_rows"] == 2
        imported = client.post("/api/v1/accounts/bulk/import", json={"raw_lines": raw, "group_id": group_id}).json()
        assert imported == {"imported": 1, "duplicates": 1, "invalid": 2, "account_ids": imported["account_ids"]}


def test_secret_cipher_round_trip_is_not_plaintext() -> None:
    plaintext = {"password": "password-value", "recovery": "recover@example.com", "2fa": "totp-value"}
    encrypted = SecretCipher().encrypt(plaintext)
    assert "password-value" not in encrypted
    assert SecretCipher().decrypt(encrypted) == plaintext


def test_secret_cipher_reads_old_key_and_rotates_to_current_key() -> None:
    original_key = settings.secret_key
    original_previous = settings.secret_key_previous
    plaintext = {"provider_key": "rotation-test-value"}
    try:
        settings.secret_key = "previous-key-for-rotation-test"
        settings.secret_key_previous = None
        old_ciphertext = SecretCipher().encrypt(plaintext)
        settings.secret_key = "current-key-for-rotation-test"
        settings.secret_key_previous = "previous-key-for-rotation-test"
        cipher = SecretCipher()
        assert cipher.decrypt(old_ciphertext) == plaintext
        rotated = cipher.rotate(old_ciphertext)
        assert SecretCipher._derive(settings.secret_key).decrypt(rotated.encode())
        assert cipher.decrypt(rotated) == plaintext
    finally:
        settings.secret_key = original_key
        settings.secret_key_previous = original_previous


def test_upload_reader_rejects_oversize_before_reading_entire_file() -> None:
    import asyncio
    from io import BytesIO

    from fastapi import HTTPException, UploadFile
    from app.main import _read_upload_bounded

    upload = UploadFile(filename="large.txt", file=BytesIO(b"x" * 100))
    try:
        asyncio.run(_read_upload_bounded(upload, 16))
        assert False, "the upload should be rejected at the configured limit"
    except HTTPException as exc:
        assert exc.status_code == 413


def test_bulk_import_idempotency_key_does_not_duplicate_accounts() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        payload = {"raw_lines": "idempotent@gmail.com:pass:recover@example.com:totp", "group_id": group_id, "idempotency_key": "import-idempotent-1"}
        first = client.post("/api/v1/accounts/bulk/import", json=payload).json()
        second = client.post("/api/v1/accounts/bulk/import", json=payload).json()
        assert second == first
        assert client.get("/api/v1/accounts").json()["total"] == 1


def test_failed_browser_launch_does_not_block_retry(monkeypatch) -> None:
    from app import service

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "retry@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()

        monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (False, None))
        failed = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert failed.status_code == 422
        assert "launched" in failed.json()["detail"].lower()

        monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (True, 4242))
        opened = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert opened.status_code == 201
        assert opened.json()["browser_opened"] is True


def test_browser_desktop_can_focus_an_existing_account_window(monkeypatch) -> None:
    from app import main

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "focus@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        focused_profiles = []
        monkeypatch.setattr(main, "local_browser_is_alive", lambda _profile_id, _session_id: True)
        monkeypatch.setattr(main, "focus_local_browser_profile", lambda profile_id: focused_profiles.append(profile_id) or True)
        response = client.post(f"/api/v1/sessions/{session['id']}/focus")

    assert response.status_code == 200
    assert response.json() == {"focused": True}
    assert focused_profiles == [session["profile_id"]]


def test_login_session_reserves_profile_and_enforces_one_open_session() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "session@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        opened = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert opened.status_code == 201
        session = opened.json()
        assert session["state"] == "LOGIN_REQUIRED"
        assert session["login_state"] == "REQUIRED"
        assert session["profile_status"] == "OPEN"
        assert session["browser_opened"] is True
        assert "secret" not in opened.text
        conflict = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert conflict.status_code == 409

        completed = client.post(f"/api/v1/sessions/{session['id']}/events", json={"event_type": "LOGIN_COMPLETED"})
        assert completed.status_code == 200
        assert completed.json()["state"] == "RUNNING"
        assert completed.json()["login_state"] == "AUTHENTICATED"

        closed = client.post(f"/api/v1/sessions/{session['id']}/close")
        assert closed.status_code == 200
        assert closed.json()["state"] == "CLOSED"
        assert closed.json()["profile_status"] == "RESERVED"
        assert client.get(f"/api/v1/sessions?account_id={account['id']}").json()[0]["id"] == session["id"]


def test_dead_browser_session_is_recovered_before_retry(monkeypatch) -> None:
    from app import service

    service.get_settings().browser_launch_enabled = True
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "stale-browser@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        first = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert first.status_code == 201
        monkeypatch.setattr(service, "local_browser_is_alive", lambda _profile_id, _session_id: False)

        second = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert second.status_code == 201
        history = client.get(f"/api/v1/sessions?account_id={account['id']}").json()
        assert history[0]["state"] == "LOGIN_REQUIRED"
        assert history[1]["state"] == "CLOSED"


def test_verification_event_is_visible_without_exposing_secrets() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "verify@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        response = client.post(f"/api/v1/sessions/{session['id']}/events", json={"event_type": "VERIFICATION_REQUIRED", "detail": "Additional verification required"})
        assert response.status_code == 200
        assert response.json()["state"] == "NEEDS_ATTENTION"
        assert response.json()["login_state"] == "VERIFICATION_REQUIRED"
        assert "secret" not in response.text


def test_page_observation_classifies_normal_pages_and_pauses_on_challenges() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "observe@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()

        normal = client.post(f"/api/v1/sessions/{session['id']}/observations", json={"url": "https://accounts.google.com/", "title": "Sign in - Google Accounts", "text": "Email or phone"})
        assert normal.status_code == 200
        assert normal.json()["signal"] == "LOGIN_FORM"
        assert normal.json()["action"] == "SAFE_TO_OBSERVE"
        assert normal.json()["session"]["state"] == "LOGIN_REQUIRED"

        challenge = client.post(f"/api/v1/sessions/{session['id']}/observations", json={"url": "https://accounts.google.com/", "title": "Unusual traffic", "text": "Please complete the CAPTCHA secret-value"})
        assert challenge.status_code == 200
        assert challenge.json()["signal"] == "CAPTCHA"
        assert challenge.json()["action"] == "WAIT_FOR_HUMAN"
        assert challenge.json()["session"]["state"] == "NEEDS_ATTENTION"
        assert "secret-value" not in challenge.text


def test_unknown_page_is_stopped_for_operator_attention() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "unknown@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        response = client.post(f"/api/v1/sessions/{session['id']}/observations", json={"url": "https://example.invalid/unexpected", "title": "Unexpected page", "text": ""})
        assert response.status_code == 200
        assert response.json()["signal"] == "UNKNOWN"
        assert response.json()["action"] == "NEEDS_ATTENTION"
        assert response.json()["session"]["state"] == "NEEDS_ATTENTION"


def test_nonexistent_account_is_detected_and_reported() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "ghost@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        response = client.post(f"/api/v1/sessions/{session['id']}/observations", json={"url": "https://accounts.google.com/signin/v2/identifier", "title": "Sign in - Google Accounts", "text": "Couldn't find your Google Account"})
        assert response.status_code == 200
        body = response.json()
        assert body["signal"] == "ACCOUNT_NOT_FOUND"
        assert body["action"] == "NEEDS_ATTENTION"
        assert body["session"]["state"] == "CLOSED"
        assert body["session"]["login_state"] == "ACCOUNT_NOT_FOUND"
        assert body["session"]["browser_opened"] is False
        assert body["session"]["profile_status"] == "RESERVED"
        assert body["session"]["profile_saved"] is False
        assert "secret" not in response.text
        ghost = next(a for a in client.get("/api/v1/accounts").json()["items"] if a["email"] == "ghost@example.com")
        assert ghost["login_status"] == "NONEXISTENT"
        assert ghost["readiness_status"] == "SETUP_ERROR"


def test_classifier_detects_google_account_not_found_copy() -> None:
    from app.login_policy import PageObservation, classify_page
    signal, action = classify_page(PageObservation(url="https://accounts.google.com/signin/v2/identifier", title="Sign in - Google Accounts", text="We couldn't find your Google Account"))
    assert signal.value == "ACCOUNT_NOT_FOUND"
    assert action.value == "NEEDS_ATTENTION"
    signal, action = classify_page(PageObservation(url="https://accounts.google.com/", title="Sign in - Google Accounts", text="No Google Account found with this email address"))
    assert signal.value == "ACCOUNT_NOT_FOUND"
    signal, action = classify_page(PageObservation(url="https://accounts.google.com/signin/v2/identifier", title="Connexion - Google", text="Impossible de trouver ce compte"))
    assert signal.value == "ACCOUNT_NOT_FOUND"


def test_classifier_accepts_sparse_post_auth_google_account_root_in_supported_languages() -> None:
    from app.login_policy import PageObservation, classify_page

    for title, text in (
        ("Google Account", ""),
        ("Compte Google", "Accueil"),
        ("Cuenta de Google", "Inicio"),
    ):
        signal, action = classify_page(PageObservation(url="https://myaccount.google.com/?pli=1", title=title, text=text))
        assert signal.value == "GOOGLE_ACCOUNT_HOME"
        assert action.value == "SAFE_TO_OBSERVE"

    # The canonical root can be momentarily text-light while Google finishes
    # painting the account shell. It must not be downgraded to UNKNOWN after
    # the OTP redirect has already reached myaccount.google.com.
    signal, action = classify_page(PageObservation(url="https://myaccount.google.com/?pli=1"))
    assert signal.value == "GOOGLE_ACCOUNT_HOME"
    assert action.value == "SAFE_TO_OBSERVE"


def test_classifier_accepts_google_oauth_addon_consent_as_expected_interstitial() -> None:
    from app.login_policy import PageObservation, classify_page

    signal, action = classify_page(PageObservation(
        url="https://accounts.google.com/signin/oauth/id?authuser=0",
        title="Acceso: Cuentas de Google",
        text="Accede a Any.do Gmail Add-on. Google permitirá que Any.do Gmail Add-on acceda a la siguiente información sobre ti. Continuar",
    ))
    assert signal.value == "ORDINARY_INTERSTITIAL"
    assert action.value == "SAFE_TO_OBSERVE"


def test_signed_in_google_account_home_marks_account_authenticated() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "post-auth@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        response = client.post(f"/api/v1/sessions/{session['id']}/observations", json={"url": "https://myaccount.google.com/?pli=1", "title": "Google Account", "text": "Google Account Personal info Security Data & privacy"})
        assert response.status_code == 200
        body = response.json()
        assert body["signal"] == "GOOGLE_ACCOUNT_HOME"
        assert body["session"]["state"] == "RUNNING"
        assert body["session"]["login_state"] == "AUTHENTICATED"
        assert body["session"]["profile_saved"] is True
        stored = next(a for a in client.get("/api/v1/accounts").json()["items"] if a["email"] == "post-auth@example.com")
        assert stored["login_status"] == "AUTHENTICATED"
        assert stored["profile"]["status"] == "SAVED"


def test_credential_update_creates_encrypted_revision_and_requires_reauth() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "rotate@example.com", "password": "old-secret", "recovery": "old@example.com", "2fa": "old-totp", "group_id": group_id}).json()
        updated = client.put(f"/api/v1/accounts/{account['id']}/credentials", json={"password": "new-secret", "recovery": "new@example.com", "2fa": "new-totp"})
        assert updated.status_code == 200
        assert "new-secret" not in updated.text
        assert "new@example.com" not in updated.text
        assert updated.json()["login_status"] == "REQUIRED"
        revisions = client.get(f"/api/v1/accounts/{account['id']}/credential-revisions")
        assert revisions.status_code == 200
        assert [row["version"] for row in revisions.json()] == [2, 1]
        assert all(row["secret_status"] == "STORED_ENCRYPTED" for row in revisions.json())


def test_credential_update_can_rotate_one_field_without_returning_or_losing_other_secrets() -> None:
    from app.db import SessionLocal
    from app.models import AccountSecret

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "partial-rotate@example.com", "password": "keep-password", "recovery": "keep@example.com", "2fa": "keep-totp", "group_id": group_id}).json()
        updated = client.put(f"/api/v1/accounts/{account['id']}/credentials", json={"recovery": "new@example.com"})
        assert updated.status_code == 200
        assert updated.json()["recovery_masked"].startswith("n")
        assert updated.json()["two_fa_configured"] is True
        assert "keep-password" not in updated.text
        assert "new@example.com" not in updated.text

        with SessionLocal() as db:
            secret = db.query(AccountSecret).filter(AccountSecret.account_id == account["id"]).one()
            assert secret is not None
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["password"] == "keep-password"
            assert payload["recovery"] == "new@example.com"
            assert payload["2fa"] == "keep-totp"


def test_credential_change_plan_full_lifecycle_rotates_revision_and_provisions_totp() -> None:
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models import AccountSecret, BrowserSession, CredentialRevision, GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "rotate-plan@example.com", "password": "old-secret", "recovery": "old@example.com", "2fa": "old-totp", "group_id": group_id},
        ).json()
        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            row.login_status = "AUTHENTICATED"
            row.profile_status = "SAVED"
            row.browser_profile.status = "SAVED"
            row.browser_profile.authenticated_at = datetime.now(timezone.utc)
            row.browser_profile.active_session_id = None
            db.commit()
        finally:
            db.close()

        created = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True, "recovery": True, "2fa": True, "recovery_emails": ["recovery-new@example.com", "backup@example.com"]},
        )
        assert created.status_code == 201
        plan = created.json()
        assert plan["status"] == "DRAFT"
        assert plan["version"] == 1
        assert plan["recovery_hint"].startswith("r") and "•" in plan["recovery_hint"]
        assert plan["recovery_count"] == 2
        assert "old-secret" not in created.text
        assert "recovery-new@example.com" not in created.text  # pool and hint stay masked

        # Identical retries return the same plan; a conflicting field set retires
        # the stale plan and prepares the newly chosen fields so a fresh selection
        # is never stuck behind an older choice.
        retried = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True, "recovery": True, "2fa": True, "recovery_emails": ["recovery-new@example.com", "backup@example.com"]},
        )
        assert retried.status_code == 201
        assert retried.json()["id"] == plan["id"]
        repicked = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True, "recovery": True, "2fa": True, "recovery_emails": ["recovery-new@example.com"]},
        )
        assert repicked.status_code == 201
        assert repicked.json()["status"] == "DRAFT"
        assert repicked.json()["version"] == 2
        assert repicked.json()["recovery_count"] == 1

        started = client.post(f"/api/v1/credential-change-plans/{repicked.json()['id']}/start")
        assert started.status_code == 200
        started_plan = started.json()
        assert started_plan["status"] == "READY_FOR_OPERATOR"
        assert started_plan["session_id"]

        # Reject confirmations that are not acknowledged.
        denied = client.post(f"/api/v1/credential-change-plans/{repicked.json()['id']}/confirm", json={"acknowledged": False})
        assert denied.status_code == 422

        # The password, 2FA and recovery address are all provisioned and typed by
        # the automated observer, so the confirm stores exactly what Google was
        # given: an operator entry outside the rotation pool can no longer desync
        # the vault from the live account.
        confirmed = client.post(
            f"/api/v1/credential-change-plans/{repicked.json()['id']}/confirm",
            json={"acknowledged": True, "password": "fresh-secret", "recovery": "outside-pool@example.com"},
        )
        assert confirmed.status_code == 200
        result = confirmed.json()
        assert result["status"] == "STORED_ENCRYPTED"
        assert result["provisioned_two_fa_secret"]  # server-generated TOTP returned exactly once
        assert result["confirmed_at"]
        # Plaintext secrets never echo back through the API.
        assert "fresh-secret" not in confirmed.text
        assert "outside-pool@example.com" not in confirmed.text

        detail = client.get(f"/api/v1/accounts/{account['id']}")
        assert detail.json()["login_status"] == "REQUIRED"
        assert detail.json()["two_fa_configured"] is True
        assert detail.json()["profile"]["status"] == "RESERVED"
        # Confirming ends the run: the observer/session is closed so it cannot
        # re-open the Google change page after the operator already confirmed.
        with SessionLocal() as session_db:
            confirmed_session = session_db.get(BrowserSession, started_plan["session_id"])
            assert confirmed_session is not None
            assert confirmed_session.state == "CLOSED"
            assert confirmed_session.login_state == "CREDENTIAL_CHANGE_COMPLETED"
        revisions = client.get(f"/api/v1/accounts/{account['id']}/credential-revisions").json()
        assert [row["version"] for row in revisions] == [2, 1]

        with SessionLocal() as vault_db:
            secret = vault_db.query(AccountSecret).filter(AccountSecret.account_id == account["id"]).one()
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["password"] != "old-secret"
            assert payload["password"] and len(payload["password"]) >= 12
            assert payload["recovery"] == "recovery-new@example.com"
            assert payload["2fa"] == result["provisioned_two_fa_secret"]
            active = vault_db.query(CredentialRevision).filter(CredentialRevision.account_id == account["id"], CredentialRevision.status == "ACTIVE").one()
            assert active.version == 2
            assert vault_db.query(CredentialRevision).filter(CredentialRevision.account_id == account["id"], CredentialRevision.version == 1).one().status == "RETIRED"

        # A new plan on the same account can still be created and cancelled without rotating.
        cancelled = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True},
        )
        assert cancelled.status_code == 201
        cancelled_plan = client.post(f"/api/v1/credential-change-plans/{cancelled.json()['id']}/cancel")
        assert cancelled_plan.status_code == 200
        assert cancelled_plan.json()["status"] == "CANCELLED"

        audit = client.get(f"/api/v1/audit-events?account_id={account['id']}&limit=100").json()
        assert any(event["action"] == "credential_change_plan.confirmed" for event in audit)
        assert any(event["action"] == "credential_change_plan.cancelled" for event in audit)


def test_completed_automated_change_autosaves_the_new_password_without_manual_confirm() -> None:
    from datetime import datetime, timezone

    from app import service
    from app.db import SessionLocal
    from app.models import AccountSecret, CredentialRevision, GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "autosave@example.com", "password": "old-secret", "recovery": "old@example.com", "2fa": "old-totp", "group_id": group_id},
        ).json()
        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            row.login_status = "AUTHENTICATED"
            row.profile_status = "SAVED"
            row.browser_profile.status = "SAVED"
            row.browser_profile.authenticated_at = datetime.now(timezone.utc)
            row.browser_profile.active_session_id = None
            db.commit()
        finally:
            db.close()

        plan = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True, "recovery": False, "2fa": True},
        ).json()
        started = client.post(f"/api/v1/credential-change-plans/{plan['id']}/start").json()
        session_id = started["session_id"]
        material = service._resolve_credential_change_material(session_id)
        new_password = material["password"]
        assert new_password and new_password != "old-secret"
        assert material["2fa"]

        # The observer reports Google accepted the change; the vault must now
        # hold the password the automation typed, with no operator confirm.
        service._report_observer_event(session_id, "credential_change.completed", "the new password was applied inside the saved Google session")

        stored = client.get(f"/api/v1/accounts/{account['id']}/credentials/plaintext")
        assert stored.status_code == 200
        assert stored.json()["password"] == new_password
        assert stored.json()["two_fa_configured"] is True

        plans = client.get(f"/api/v1/accounts/{account['id']}/credential-change-plans").json()
        assert plans[0]["status"] == "STORED_ENCRYPTED"
        assert client.get(f"/api/v1/accounts/{account['id']}").json()["login_status"] == "REQUIRED"

        with SessionLocal() as vault_db:
            secret = vault_db.query(AccountSecret).filter(AccountSecret.account_id == account["id"]).one()
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["password"] == new_password
            assert payload["recovery"] == "old@example.com"
            active = vault_db.query(CredentialRevision).filter(CredentialRevision.account_id == account["id"], CredentialRevision.status == "ACTIVE").one()
            assert active.version == 2

        audit = client.get(f"/api/v1/audit-events?account_id={account['id']}&limit=100").json()
        assert any(event["action"] == "credential_change_plan.confirmed" for event in audit)

        # A late manual confirm on the already-stored plan is idempotent.
        repeat = client.post(f"/api/v1/credential-change-plans/{plan['id']}/confirm", json={"acknowledged": True})
        assert repeat.status_code == 200
        assert repeat.json()["status"] == "STORED_ENCRYPTED"


def test_captured_authenticator_key_autosaves_and_replaces_the_old_one() -> None:
    from datetime import datetime, timezone

    from app import service
    from app.db import SessionLocal
    from app.models import AccountSecret, CredentialRevision, GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "captured-key@example.com", "password": "fixed-password", "recovery": "keep@example.com", "2fa": "OLDROTATEDSECRET", "group_id": group_id},
        ).json()
        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            org_id = row.organization_id
            row.login_status = "AUTHENTICATED"
            row.profile_status = "SAVED"
            row.browser_profile.status = "SAVED"
            row.browser_profile.authenticated_at = datetime.now(timezone.utc)
            row.browser_profile.active_session_id = None
            db.commit()
        finally:
            db.close()

        plan = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": False, "recovery": False, "2fa": True},
        ).json()
        started = client.post(f"/api/v1/credential-change-plans/{plan['id']}/start").json()
        session_id = started["session_id"]

        # The observer captures Google's own pairing key on the setup screen and
        # stores it, exactly like _handle_credential_change_tick does.
        material = service._resolve_credential_change_material(session_id)
        assert material["2fa"]
        captured = "JBSWY3DPEHPK3PXP"  # simulate the base32 key Google generated
        service._store_credential_change_material(session_id, org_id, {**material, "2fa": captured})

        service._report_observer_event(session_id, "credential_change.completed", "the new authenticator was added in Two-Step Verification; the secret was saved to the profile")

        stored = client.get(f"/api/v1/accounts/{account['id']}/credentials/plaintext")
        assert stored.status_code == 200
        assert stored.json()["two_fa_configured"] is True

        with SessionLocal() as vault_db:
            secret = vault_db.query(AccountSecret).filter(AccountSecret.account_id == account["id"]).one()
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["2fa"] == captured
            assert payload["2fa"] != "OLDROTATEDSECRET"
            # Untouched fields are backfilled, never blanked.
            assert payload["password"] == "fixed-password"
            assert payload["recovery"] == "keep@example.com"
            active = vault_db.query(CredentialRevision).filter(CredentialRevision.account_id == account["id"], CredentialRevision.status == "ACTIVE").one()
            assert active.version == 2


def test_password_leg_is_persisted_before_the_two_fa_leg_completes() -> None:
    from datetime import datetime, timezone

    from app import service
    from app.db import SessionLocal
    from app.models import AccountSecret, CredentialRevision, GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "pwleg@example.com", "password": "old-secret", "recovery": "old@example.com", "2fa": "old-totp", "group_id": group_id},
        ).json()
        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            row.login_status = "AUTHENTICATED"
            row.profile_status = "SAVED"
            row.browser_profile.status = "SAVED"
            row.browser_profile.authenticated_at = datetime.now(timezone.utc)
            row.browser_profile.active_session_id = None
            db.commit()
        finally:
            db.close()

        plan = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True, "recovery": False, "2fa": True},
        ).json()
        started = client.post(f"/api/v1/credential-change-plans/{plan['id']}/start").json()
        session_id = started["session_id"]
        new_password = service._resolve_credential_change_material(session_id)["password"]
        assert new_password and new_password != "old-secret"

        # Google confirms the password leg only. The vault must rotate to the new
        # password immediately, while the plan stays pending on the 2FA leg.
        service._report_observer_event(session_id, "password.change_completed", "Google confirmed the password change")

        stored = client.get(f"/api/v1/accounts/{account['id']}/credentials/plaintext")
        assert stored.json()["password"] == new_password
        with SessionLocal() as vault_db:
            secret = vault_db.query(AccountSecret).filter(AccountSecret.account_id == account["id"]).one()
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["password"] == new_password
            # The (still unverified) 2FA leg must not overwrite the old secret.
            assert payload["2fa"] == "old-totp"

        # The plan is NOT stored yet: the 2FA leg has not completed, so the UI
        # should keep it visible as still running rather than show a done plan.
        plans = client.get(f"/api/v1/accounts/{account['id']}/credential-change-plans").json()
        assert plans[0]["status"] == "READY_FOR_OPERATOR"

        # A late 2FA failure must not roll back the already-confirmed password.
        service._report_observer_event(session_id, "credential_change.failed", "Google rejected the authenticator verification code repeatedly")
        stored = client.get(f"/api/v1/accounts/{account['id']}/credentials/plaintext")
        assert stored.json()["password"] == new_password
        assert client.get(f"/api/v1/accounts/{account['id']}").json()["login_status"] == "REQUIRED"


def test_credential_change_plan_restarts_after_its_session_closes_and_blocks_while_running() -> None:
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models import BrowserSession, GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "restart-plan@example.com", "password": "old-secret", "recovery": "old@example.com", "2fa": "seed-otp", "group_id": group_id},
        ).json()
        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            row.login_status = "AUTHENTICATED"
            row.profile_status = "SAVED"
            row.browser_profile.status = "SAVED"
            row.browser_profile.authenticated_at = datetime.now(timezone.utc)
            row.browser_profile.active_session_id = None
            db.commit()
        finally:
            db.close()

        created = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True, "recovery": False, "2fa": True},
        )
        assert created.status_code == 201
        plan_id = created.json()["id"]

        # A stale READY_FOR_OPERATOR plan must not be restartable while its session is live.
        started = client.post(f"/api/v1/credential-change-plans/{plan_id}/start")
        assert started.status_code == 200
        first_session = started.json()["session_id"]
        blocked = client.post(f"/api/v1/credential-change-plans/{plan_id}/start")
        assert blocked.status_code == 422

        # Once the session is closed, the same plan can be started again fresh.
        closed = client.post(f"/api/v1/sessions/{first_session}/close")
        assert closed.status_code == 200
        restarted = client.post(f"/api/v1/credential-change-plans/{plan_id}/start")
        assert restarted.status_code == 200
        restarted_plan = restarted.json()
        assert restarted_plan["status"] == "READY_FOR_OPERATOR"
        assert restarted_plan["session_id"] and restarted_plan["session_id"] != first_session

        # The restarted plan provisions a freshly generated password for the retry.
        with SessionLocal() as vault_db:
            session_row = vault_db.query(BrowserSession).filter(BrowserSession.id == restarted_plan["session_id"]).one()
            pending = SecretCipher().decrypt(session_row.metadata_json["credential_change_pending"])
            assert pending["password"] and len(pending["password"]) >= 12
            assert pending["password"] != "old-secret"


def test_credential_records_listing_and_one_time_rotation() -> None:
    from app.db import SessionLocal

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "credentials-view@example.com", "password": "initial-secret", "recovery": "old@example.com", "2fa": "seed-totp", "group_id": group_id},
        ).json()

        response = client.get("/api/v1/credentials").json()
        row = next(item for item in response["items"] if item["email"] == "credentials-view@example.com")
        assert row["password_present"] is True
        assert row["credential_status"] == "STORED_ENCRYPTED"
        assert row["active_version"] == 1
        assert row["two_fa_configured"] is True
        assert row["recovery_masked"].startswith("o")
        assert "initial-secret" not in client.get("/api/v1/credentials").text

        stored = client.get("/api/v1/credentials", params={"status": "STORED_ENCRYPTED"}).json()
        assert any(item["email"] == "credentials-view@example.com" for item in stored["items"])
        missing = client.get("/api/v1/credentials", params={"status": "MISSING"}).json()
        assert not any(item["email"] == "credentials-view@example.com" for item in missing["items"])
        searched = client.get("/api/v1/credentials", params={"q": account["id"]}).json()
        assert any(item["email"] == "credentials-view@example.com" for item in searched["items"])

        rotated = client.post(f"/api/v1/accounts/{account['id']}/credentials/rotate", timeout=30)
        assert rotated.status_code == 200
        result = rotated.json()
        assert result["version"] == 2
        assert result["password"] and len(result["password"]) >= 12
        assert result["password"] != "initial-secret"
        # The plaintext password is returned exactly once and never re-read back.
        assert result["password"] not in client.get("/api/v1/credentials").text
        with SessionLocal() as vault_db:
            from app.models import AccountSecret
            secret = vault_db.query(AccountSecret).filter(AccountSecret.account_id == account["id"]).one()
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["password"] == result["password"]
            assert payload["recovery"] == "old@example.com"
            assert payload["2fa"] == "seed-totp"

        second = client.post(f"/api/v1/accounts/{account['id']}/credentials/rotate", timeout=30).json()
        assert second["version"] == 3
        assert second["password"] != result["password"]
        assert [row["version"] for row in client.get(f"/api/v1/accounts/{account['id']}/credential-revisions").json()] == [3, 2, 1]
        assert client.get(f"/api/v1/accounts/{account['id']}").json()["login_status"] == "REQUIRED"


def test_credential_change_material_is_read_from_plan_when_session_blob_is_lost() -> None:
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models import BrowserSession, GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "race-fallback@example.com", "password": "old-secret", "recovery": "old@example.com", "2fa": "old-totp", "group_id": group_id},
        ).json()
        db = SessionLocal()
        try:
            row = db.query(GmailAccount).filter_by(id=account["id"]).one()
            row.login_status = "AUTHENTICATED"
            row.profile_status = "SAVED"
            row.browser_profile.status = "SAVED"
            row.browser_profile.authenticated_at = datetime.now(timezone.utc)
            row.browser_profile.active_session_id = None
            db.commit()
        finally:
            db.close()

        plan = client.post(
            f"/api/v1/accounts/{account['id']}/credential-change-plans",
            json={"password": True, "2fa": True},
        ).json()
        started = client.post(f"/api/v1/credential-change-plans/{plan['id']}/start")
        assert started.status_code == 200
        session_id = started.json()["session_id"]

        # Simulate the observer lost-update race that wiped the session metadata
        # blob: the plan copy must still let confirm resolve the material.
        db = SessionLocal()
        try:
            session_row = db.query(BrowserSession).filter_by(id=session_id).one()
            metadata = dict(session_row.metadata_json or {})
            metadata.pop("credential_change_pending", None)
            session_row.metadata_json = metadata
            db.commit()
        finally:
            db.close()

        confirmed = client.post(f"/api/v1/credential-change-plans/{plan['id']}/confirm", json={"acknowledged": True})
        assert confirmed.status_code == 200
        result = confirmed.json()
        assert result["status"] == "STORED_ENCRYPTED"
        assert result["provisioned_two_fa_secret"]
        with SessionLocal() as vault_db:
            from app.models import AccountSecret
            secret = vault_db.query(AccountSecret).filter(AccountSecret.account_id == account["id"]).one()
            payload = SecretCipher().decrypt(secret.encrypted_payload)
            assert payload["password"] and payload["password"] != "old-secret"
            assert payload["2fa"] == result["provisioned_two_fa_secret"]


def test_two_fa_only_plan_start_url_is_the_authenticator_page_before_session_is_linked() -> None:
    from datetime import datetime, timezone

    from app import service
    from app.db import SessionLocal
    from app.models import BrowserSession, GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]

        def _saved_account(email: str) -> dict:
            account = client.post(
                "/api/v1/accounts/single",
                json={"email": email, "password": "old-secret", "recovery": "old@example.com", "2fa": "old-totp", "group_id": group_id},
            ).json()
            db = SessionLocal()
            try:
                row = db.query(GmailAccount).filter_by(id=account["id"]).one()
                row.login_status = "AUTHENTICATED"
                row.profile_status = "SAVED"
                row.browser_profile.status = "SAVED"
                row.browser_profile.authenticated_at = datetime.now(timezone.utc)
                row.browser_profile.active_session_id = None
                db.commit()
            finally:
                db.close()
            return account

        two_fa_account = _saved_account("twofa-start-page@example.com")
        combined_account = _saved_account("combined-start-page@example.com")
        recovery_account = _saved_account("recovery-start-page@example.com")
        assert client.post(
            f"/api/v1/accounts/{two_fa_account['id']}/credential-change-plans",
            json={"password": False, "2fa": True},
        ).json()["status"] == "DRAFT"
        assert client.post(
            f"/api/v1/accounts/{combined_account['id']}/credential-change-plans",
            json={"password": True, "2fa": True},
        ).json()["status"] == "DRAFT"
        assert client.post(
            f"/api/v1/accounts/{recovery_account['id']}/credential-change-plans",
            json={"password": False, "recovery": True, "recovery_emails": ["rotated@example.com"]},
        ).json()["status"] == "DRAFT"

        # The observer launches inside start_login_session, before the started
        # plan is linked to the session; the start page must still honour the
        # plan's requested fields through the account fallback.
        db = SessionLocal()
        try:
            rows = {a["email"]: db.query(GmailAccount).filter_by(id=a["id"]).one() for a in (two_fa_account, combined_account, recovery_account)}
            for email, session_id, expected in (
                ("twofa-start-page@example.com", "session-twofa-start-page", "https://myaccount.google.com/two-step-verification/authenticator"),
                ("combined-start-page@example.com", "session-combined-start-page", "https://myaccount.google.com/signinoptions/password"),
                ("recovery-start-page@example.com", "session-recovery-start-page", "https://myaccount.google.com/recovery/email"),
            ):
                row = rows[email]
                db.add(BrowserSession(
                    id=session_id,
                    organization_id=row.organization_id,
                    account_id=row.id,
                    profile_id=row.browser_profile.id,
                    state="RUNNING",
                    login_state="AUTHENTICATED",
                    runtime_type="LOCAL_CHROME",
                    metadata_json={},
                ))
            db.commit()
            assert service._credential_change_start_url("session-twofa-start-page", rows["twofa-start-page@example.com"].organization_id) == "https://myaccount.google.com/two-step-verification/authenticator"
            assert service._credential_change_start_url("session-combined-start-page", rows["combined-start-page@example.com"].organization_id) == "https://myaccount.google.com/signinoptions/password"
            assert service._credential_change_start_url("session-recovery-start-page", rows["recovery-start-page@example.com"].organization_id) == "https://myaccount.google.com/recovery/email"
        finally:
            db.close()
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": "copy-view@example.com", "password": "copy-me-secret", "recovery": "copybackup@example.com", "2fa": "copy-totp", "group_id": group_id},
        ).json()

        plain = client.get(f"/api/v1/accounts/{account['id']}/credentials/plaintext")
        assert plain.status_code == 200
        body = plain.json()
        assert body["email"] == "copy-view@example.com"
        assert body["password"] == "copy-me-secret"
        assert body["active_version"] == 1
        assert body["two_fa_configured"] is True
        assert body["recovery"] == "copybackup@example.com"
        assert "copy-me-secret" not in client.get("/api/v1/credentials").text

        audit = client.get("/api/v1/audit-events", params={"account_id": account["id"], "limit": 20}).json()
        assert any(event["action"] == "account.credentials_copied" for event in audit)

        rotated = client.post(f"/api/v1/accounts/{account['id']}/credentials/rotate", timeout=30).json()
        after = client.get(f"/api/v1/accounts/{account['id']}/credentials/plaintext").json()
        assert after["password"] == rotated["password"]
        assert after["active_version"] == 2


def test_login_job_queues_group_accounts_and_honors_concurrency() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        for index in range(3):
            client.post("/api/v1/accounts/single", json={"email": f"batch-{index}@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id})
        created = client.post("/api/v1/login-jobs", json={"group_id": group_id, "concurrency_limit": 2})
        assert created.status_code == 201
        job = created.json()
        assert job["status"] == "QUEUED"
        assert job["total_count"] == 3
        assert job["queued_count"] == 3
        assert job["concurrency_limit"] == 2
        assert {item["state"] for item in job["items"]} == {"QUEUED"}
        started = client.post(f"/api/v1/login-jobs/{job['id']}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "RUNNING"
        assert started.json()["running_count"] == 2
        assert client.get(f"/api/v1/login-jobs/{job['id']}/progress").json()["total_count"] == 3
        running_item = next(item for item in started.json()["items"] if item["state"] == "RUNNING")
        completed = client.post(f"/api/v1/sessions/{running_item['session_id']}/events", json={"event_type": "LOGIN_COMPLETED"})
        assert completed.status_code == 200
        progress = client.get(f"/api/v1/login-jobs/{job['id']}").json()
        assert progress["succeeded_count"] == 1
        assert progress["running_count"] == 1
        dispatched = client.post(f"/api/v1/login-jobs/{job['id']}/dispatch")
        assert dispatched.status_code == 200
        assert dispatched.json()["queued_count"] == 0
        paused = client.post(f"/api/v1/login-jobs/{job['id']}/pause")
        assert paused.status_code == 200
        assert paused.json()["status"] == "PAUSED"
        cancelled = client.post(f"/api/v1/login-jobs/{job['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"
        assert cancelled.json()["queued_count"] == 0
        assert "SUCCEEDED" in {item["state"] for item in cancelled.json()["items"]}
        assert "CANCELLED" in {item["state"] for item in cancelled.json()["items"]}


def test_browser_desktop_batch_includes_selected_accounts_needing_setup() -> None:
    from app.db import SessionLocal
    from app.models import GmailAccount

    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "desktop-setup@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        with SessionLocal() as db:
            db.get(GmailAccount, account["id"]).readiness_status = "SETUP_REQUIRED"
            db.commit()
        created = client.post("/api/v1/login-jobs", json={"account_ids": [account["id"]], "concurrency_limit": 1})

    assert created.status_code == 201
    assert created.json()["total_count"] == 1
    assert created.json()["items"][0]["account_email"] == "desktop-setup@example.com"


def test_account_metadata_can_move_and_audit_without_exposing_secrets() -> None:
    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        source_id, destination_id = groups[0]["id"], groups[1]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "admin-flow@example.com", "password": "hidden-password", "recovery": "hidden@example.com", "2fa": "hidden-totp", "group_id": source_id}).json()

        edited = client.patch(f"/api/v1/accounts/{account['id']}", json={"proxy_label": "US-NY-0142", "proxy_region": "New York, US"})
        assert edited.status_code == 200
        assert edited.json()["proxy"] == {"id": None, "label": "US-NY-0142", "endpoint": None, "region": "New York, US", "status": "ASSIGNED"}
        assert "hidden-password" not in edited.text

        moved = client.post(f"/api/v1/accounts/{account['id']}/group-move", json={"destination_group_id": destination_id})
        assert moved.status_code == 200
        assert moved.json()["group_id"] == destination_id
        audit = client.get("/api/v1/audit-events", params={"account_id": account["id"], "limit": 20}).json()
        assert {event["action"] for event in audit} >= {"account.metadata_updated", "account.group_moved"}
        assert "hidden-password" not in str(audit)


def test_operation_drafts_are_versioned_and_reject_secret_fields() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "drafts@example.com", "password": "hidden-password", "recovery": "hidden@example.com", "2fa": "hidden-totp", "group_id": group_id}).json()
        payload = {"operation_type": "labels", "configuration": {"labels": "Priority\nFollow-up"}}
        first = client.post(f"/api/v1/accounts/{account['id']}/operation-drafts", json=payload)
        second = client.post(f"/api/v1/accounts/{account['id']}/operation-drafts", json=payload)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["version"] == 1
        assert second.json()["version"] == 2
        listed = client.get(f"/api/v1/accounts/{account['id']}/operation-drafts")
        assert listed.status_code == 200
        assert [draft["version"] for draft in listed.json()] == [2, 1]
        assert "hidden-password" not in listed.text
        rejected = client.post(f"/api/v1/accounts/{account['id']}/operation-drafts", json={"operation_type": "mail", "configuration": {"password": "must-not-store"}})
        assert rejected.status_code == 422
        assert "must-not-store" not in rejected.text


def test_delete_archives_account_closes_session_and_removes_it_from_registry() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "archive-me@example.com", "password": "hidden-password", "recovery": "hidden@example.com", "2fa": "hidden-totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        deleted = client.request("DELETE", f"/api/v1/accounts/{account['id']}", json={"confirmation_email": "archive-me@example.com"})
        assert deleted.status_code == 200
        assert deleted.json()["lifecycle_status"] == "ARCHIVED"
        assert client.get("/api/v1/accounts", params={"q": "archive-me@example.com"}).json()["total"] == 0
        assert client.get(f"/api/v1/sessions/{session['id']}").json()["state"] == "CLOSED"
        blocked = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert blocked.status_code == 409
        assert "archived" in blocked.json()["detail"].lower()
        audit = client.get("/api/v1/audit-events", params={"account_id": account["id"], "limit": 20}).json()
        assert any(event["action"] == "account.archived" for event in audit)
        assert any(event["target_id"] == session["id"] for event in audit)
        assert "hidden-password" not in str(audit)


def test_readded_account_is_reactivated_with_fresh_credentials() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        payload = {"email": "re-add@example.com", "password": "old-secret", "recovery": "old@example.com", "2fa": "old-totp", "group_id": group_id}
        first = client.post("/api/v1/accounts/single", json=payload).json()
        deleted = client.request("DELETE", f"/api/v1/accounts/{first['id']}", json={"confirmation_email": "re-add@example.com"})
        assert deleted.status_code == 200
        assert deleted.json()["lifecycle_status"] == "ARCHIVED"

        refreshed = dict(payload, password="new-secret", recovery="new@example.com", two_fa="new-totp", **{"2fa": "new-totp"})
        readded = client.post("/api/v1/accounts/single", json=refreshed)
        assert readded.status_code == 201
        body = readded.json()
        assert body["id"] == first["id"]
        assert body["lifecycle_status"] == "SETUP_REQUIRED"
        assert body["secret_status"] == "STORED_ENCRYPTED"
        assert body["profile"]["status"] == "RESERVED"
        assert "new-secret" not in readded.text

        listed = client.get("/api/v1/accounts", params={"q": "re-add@example.com"}).json()
        assert listed["total"] == 1

        audit = client.get("/api/v1/audit-events", params={"account_id": first["id"], "limit": 20}).json()
        assert any(event["action"] == "account.reactivated" for event in audit)

        from sqlalchemy import select
        from app.db import SessionLocal
        from app.security import SecretCipher
        from app.models import GmailAccount, CredentialRevision

        db = SessionLocal()
        try:
            account = db.get(GmailAccount, first["id"])
            secrets = SecretCipher().decrypt(account.secret.encrypted_payload)
            assert secrets["password"] == "new-secret"
            assert secrets["2fa"] == "new-totp"
            latest = db.scalar(select(CredentialRevision).where(CredentialRevision.account_id == first["id"]).order_by(CredentialRevision.version.desc()))
            assert latest is not None and latest.version >= 2
            assert account.browser_profile.authenticated_at is None
        finally:
            db.close()


def test_bulk_reimport_reactivates_archived_account() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        raw = "bulk-readd@example.com:old-s:pw@example.com:oldt"
        first = client.post("/api/v1/accounts/bulk/import", json={"raw_lines": raw, "group_id": group_id, "idempotency_key": "bulk-readd-1"}).json()
        assert first["imported"] == 1
        account_id = first["account_ids"][0]
        client.request("DELETE", f"/api/v1/accounts/{account_id}", json={"confirmation_email": "bulk-readd@example.com"})

        while_archived = client.post("/api/v1/accounts/bulk/preview", json={"raw_lines": raw, "group_id": group_id}).json()
        assert while_archived["valid_rows"] == 1
        assert while_archived["duplicate_rows"] == 0

        second = client.post("/api/v1/accounts/bulk/import", json={"raw_lines": raw, "group_id": group_id, "idempotency_key": "bulk-readd-2"}).json()
        assert second["imported"] == 1
        assert second["account_ids"] == first["account_ids"]


def test_active_account_still_rejects_duplicate_email() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        payload = {"email": "still-active@example.com", "password": "s", "recovery": "r@example.com", "2fa": "t", "group_id": group_id}
        client.post("/api/v1/accounts/single", json=payload)
        duplicate = client.post("/api/v1/accounts/single", json=payload)
        assert duplicate.status_code == 422
        assert "already exists" in duplicate.json()["detail"]


def test_profile_folder_is_named_after_the_account_email() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        created = client.post("/api/v1/accounts/single", json={"email": "Jane.Doe+ops@Example.com", "password": "s", "recovery": "r@example.com", "2fa": "t", "group_id": group_id})
        assert created.status_code == 201
        body = created.json()
        assert body["profile"]["id"] == "jane.doe+ops@example.com__org_demo"
        assert body["profile"]["status"] == "RESERVED"
        listed = client.get("/api/v1/accounts").json()["items"]
        assert any(a["profile"]["id"] == "jane.doe+ops@example.com__org_demo" for a in listed)


def test_closed_unauthenticated_session_discards_its_profile_folder(tmp_path, monkeypatch) -> None:
    from app.main import settings as app_settings
    monkeypatch.setattr(app_settings, "profile_root", str(tmp_path))
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "unconnected@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        folder = tmp_path / account["profile"]["id"]
        folder.mkdir(parents=True)
        (folder / "Cookies").write_text("scratch")
        client.post(f"/api/v1/sessions/{session['id']}/close").json()
        assert not folder.exists(), "an unconnected account must not keep a saved profile"
        assert (tmp_path / account["profile"]["id"]).exists() is False


def test_completed_login_keeps_its_saved_profile_folder(tmp_path, monkeypatch) -> None:
    from app.main import settings as app_settings
    monkeypatch.setattr(app_settings, "profile_root", str(tmp_path))
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "connected@example.com", "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id}).json()
        session = client.post(f"/api/v1/accounts/{account['id']}/sessions").json()
        folder = tmp_path / account["profile"]["id"]
        folder.mkdir(parents=True)
        (folder / "Cookies").write_text("session-cookies")
        client.post(f"/api/v1/sessions/{session['id']}/events", json={"event_type": "LOGIN_COMPLETED"})
        client.post(f"/api/v1/sessions/{session['id']}/close").json()
        assert folder.exists(), "a fully connected account keeps its saved profile"
        assert (folder / "Cookies").read_text() == "session-cookies"


def test_workspace_structure_controls_are_audited() -> None:
    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        group = groups[0]
        edited_group = client.patch(f"/api/v1/groups/{group['id']}", json={"name": "Edited group", "description": "Managed boundary", "capacity": 450, "status": "PAUSED"})
        assert edited_group.status_code == 200
        assert edited_group.json()["status"] == "PAUSED"

        created = client.post("/api/v1/employees", json={"full_name": "Jamie Rivera", "email": "jamie@example.com", "role": "AUDITOR"})
        assert created.status_code == 201
        employee_id = created.json()["id"]
        assert client.patch(f"/api/v1/employees/{employee_id}", json={"role": "ACCOUNT_OPERATOR", "status": "SUSPENDED"}).json()["status"] == "SUSPENDED"
        assert client.delete(f"/api/v1/employees/{employee_id}").json()["status"] == "DEACTIVATED"

        saved = client.put("/api/v1/settings", json={"default_group_capacity": 450, "default_login_concurrency": 8, "auto_close_completed_sessions": False})
        assert saved.status_code == 200
        assert saved.json()["default_group_capacity"] == 450
        assert saved.json()["auto_close_completed_sessions"] is False
        events = client.get("/api/v1/audit-events?limit=100").json()
        actions = {event["action"] for event in events}
        assert {"group.updated", "employee.created", "employee.updated", "workspace_settings.updated"}.issubset(actions)


def test_employee_group_access_is_scoped_replaceable_and_audited() -> None:
    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        employee = client.post("/api/v1/employees", json={"full_name": "Group Operator", "email": "operator@example.com", "role": "ACCOUNT_OPERATOR"}).json()
        second_employee = client.post("/api/v1/employees", json={"full_name": "Second Operator", "email": "operator-two@example.com", "role": "ACCOUNT_OPERATOR"}).json()
        employee_id = employee["id"]
        saved = client.put(f"/api/v1/employees/{employee_id}/group-access", json={"assignments": [{"group_id": groups[0]["id"], "access_level": "OPERATOR"}, {"group_id": groups[1]["id"], "access_level": "VIEWER"}]})
        assert saved.status_code == 200
        assert {(item["group_id"], item["access_level"]) for item in saved.json()["group_access"]} == {(groups[0]["id"], "OPERATOR"), (groups[1]["id"], "VIEWER")}
        assert client.get(f"/api/v1/employees/{employee_id}/group-access").json()[1]["group_name"] == groups[1]["name"]
        # Group membership is many-to-many: assigning the same group to a
        # second user must not replace or alter the first user's access.
        shared = client.put(f"/api/v1/employees/{second_employee['id']}/group-access", json={"assignments": [{"group_id": groups[0]["id"], "access_level": "VIEWER"}]})
        assert shared.status_code == 200
        assert shared.json()["group_access"] == [{"group_id": groups[0]["id"], "group_name": groups[0]["name"], "access_level": "VIEWER"}]
        assert {(item["group_id"], item["access_level"]) for item in client.get(f"/api/v1/employees/{employee_id}/group-access").json()} == {(groups[0]["id"], "OPERATOR"), (groups[1]["id"], "VIEWER")}
        assert client.patch(f"/api/v1/employees/{employee_id}", json={"status": "SUSPENDED"}).json()["group_access"][0]["access_level"] == "OPERATOR"

        replaced = client.put(f"/api/v1/employees/{employee_id}/group-access", json={"assignments": [{"group_id": groups[2]["id"], "access_level": "MANAGER"}]})
        assert replaced.status_code == 200
        assert replaced.json()["group_access"] == [{"group_id": groups[2]["id"], "group_name": groups[2]["name"], "access_level": "MANAGER"}]
        duplicate = client.put(f"/api/v1/employees/{employee_id}/group-access", json={"assignments": [{"group_id": groups[2]["id"]}, {"group_id": groups[2]["id"]}]})
        assert duplicate.status_code == 422
        events = client.get("/api/v1/audit-events?limit=100").json()
        assert sum(event["action"] == "employee.group_access.updated" for event in events) == 3


def test_employee_login_and_group_scope_are_enforced() -> None:
    from app.main import _AUTH_LIMIT_BUCKETS, _AUTH_LIMIT_LOCK

    with _AUTH_LIMIT_LOCK:
        _AUTH_LIMIT_BUCKETS.clear()
    original_auth_required = settings.auth_required
    settings.auth_required = True
    try:
        with TestClient(app) as client:
            setup = client.post("/api/v1/auth/setup", json={"email": "owner@example.com", "password": "OwnerPassword123!"})
            assert setup.status_code == 201
            groups = client.get("/api/v1/groups").json()
            employee = client.post("/api/v1/employees", json={"full_name": "Scoped Operator", "email": "operator@example.com", "role": "ACCOUNT_OPERATOR"}).json()
            password = client.put(f"/api/v1/employees/{employee['id']}/password", json={"password": "OperatorPassword123!"})
            assert password.status_code == 204
            access = client.put(f"/api/v1/employees/{employee['id']}/group-access", json={"assignments": [{"group_id": groups[0]["id"], "access_level": "MANAGER"}]})
            assert access.status_code == 200
            first = client.post("/api/v1/accounts/single", json={"email": "inside@example.com", "password": "secret", "recovery": "recover@example.com", "2fa": "totp", "group_id": groups[0]["id"]})
            second = client.post("/api/v1/accounts/single", json={"email": "outside@example.com", "password": "secret", "recovery": "recover@example.com", "2fa": "totp", "group_id": groups[1]["id"]})
            assert first.status_code == 201 and second.status_code == 201
            assert client.post("/api/v1/auth/logout").status_code == 204
            login = client.post("/api/v1/auth/login", json={"email": "operator@example.com", "password": "OperatorPassword123!"})
            assert login.status_code == 200
            assert login.json()["employee"]["email"] == "operator@example.com"
            visible = client.get("/api/v1/accounts").json()
            assert [item["email"] for item in visible["items"]] == ["inside@example.com"]
            assert client.get(f"/api/v1/accounts/{second.json()['id']}").status_code == 404
            assert client.get(f"/api/v1/accounts/{first.json()['id']}/credentials/plaintext").status_code == 403
            outside_bulk = client.post("/api/v1/accounts/bulk/import", json={"raw_lines": "blocked@example.com:secret", "group_id": groups[1]["id"]})
            assert outside_bulk.status_code == 404
            outside_preview = client.post("/api/v1/accounts/bulk/preview", json={"raw_lines": "blocked@example.com:secret", "group_id": groups[1]["id"]})
            assert outside_preview.status_code == 404
            outside_move = client.post(f"/api/v1/accounts/{first.json()['id']}/group-move", json={"destination_group_id": groups[1]["id"]})
            assert outside_move.status_code == 404
            assert [group["id"] for group in client.get("/api/v1/groups").json()] == [groups[0]["id"]]
            assert client.get("/api/v1/employees").status_code == 403
            assert client.get("/api/v1/settings").status_code == 403
            client.post("/api/v1/auth/logout")
            for _ in range(5):
                assert client.post("/api/v1/auth/login", json={"email": "operator@example.com", "password": "wrong-password"}).status_code == 401
            assert client.post("/api/v1/auth/login", json={"email": "operator@example.com", "password": "OperatorPassword123!"}).status_code == 401
    finally:
        settings.auth_required = original_auth_required


def test_newsletter_agent_settings_are_masked_and_loaded_only_for_runtime() -> None:
    from app.db import SessionLocal
    from app.models import WorkspaceSettings
    from app.service import _newsletter_agent_runtime_settings

    with TestClient(app) as client:
        saved = client.put(
            "/api/v1/settings",
            json={
                "newsletter_agent_enabled": True,
                "newsletter_agent_provider": "openrouter",
                "newsletter_agent_model": "openrouter/free",
                "newsletter_agent_temperature": 0,
                "newsletter_agent_max_steps": 12,
                "newsletter_agent_timeout_seconds": 30,
                "newsletter_agent_shadow_mode": True,
                "newsletter_agent_live_execution_enabled": False,
                "openrouter_api_key": "test-openrouter-key",
            },
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["newsletter_agent_enabled"] is True
        assert body["newsletter_agent_model"] == "openrouter/free"
        assert body["openrouter_api_key_configured"] is True
        assert "openrouter_api_key" not in body
        assert "test-openrouter-key" not in saved.text

    with SessionLocal() as db:
        row = db.query(WorkspaceSettings).one()
        assert row.openrouter_api_key_encrypted
        assert "test-openrouter-key" not in row.openrouter_api_key_encrypted
        runtime = _newsletter_agent_runtime_settings(db, settings.demo_org_id)
        assert runtime["enabled"] is True
        assert runtime["api_key"] == "test-openrouter-key"
        assert runtime["shadow_mode"] is True
