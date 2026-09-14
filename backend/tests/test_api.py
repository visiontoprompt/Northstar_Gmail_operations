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


def test_production_api_requires_bearer_token() -> None:
    original_environment = settings.environment
    original_token = settings.admin_api_token
    settings.environment = "production"
    settings.admin_api_token = "test-admin-token"
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/api/v1/accounts").status_code == 401
            assert client.get("/api/v1/accounts", headers={"Authorization": "Bearer test-admin-token"}).status_code == 200
    finally:
        settings.environment = original_environment
        settings.admin_api_token = original_token


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


def test_account_metadata_can_move_and_audit_without_exposing_secrets() -> None:
    with TestClient(app) as client:
        groups = client.get("/api/v1/groups").json()
        source_id, destination_id = groups[0]["id"], groups[1]["id"]
        account = client.post("/api/v1/accounts/single", json={"email": "admin-flow@example.com", "password": "hidden-password", "recovery": "hidden@example.com", "2fa": "hidden-totp", "group_id": source_id}).json()

        edited = client.patch(f"/api/v1/accounts/{account['id']}", json={"proxy_label": "US-NY-0142", "proxy_region": "New York, US"})
        assert edited.status_code == 200
        assert edited.json()["proxy"] == {"id": None, "label": "US-NY-0142", "region": "New York, US", "status": "ASSIGNED"}
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
        assert "hidden-password" not in str(audit)
