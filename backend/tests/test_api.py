import os

os.environ["DATABASE_URL"] = "sqlite:///./data/test_gmail_ops.db"
os.environ["SECRET_KEY"] = "test-secret-key-without-credential-material"

from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.security import SecretCipher


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app):
        pass


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
        assert session["browser_opened"] is False
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
