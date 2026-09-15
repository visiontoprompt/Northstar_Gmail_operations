import os

os.environ["DATABASE_URL"] = "sqlite:///./data/test_gmail_ops.db"
os.environ["SECRET_KEY"] = "test-secret-key-without-credential-material"
os.environ["REDIS_URL"] = "fakeredis://"

from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.worker import redis as worker_redis


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    worker_redis.reset_redis()
    worker_redis.flush_redis()
    with TestClient(app):
        pass


def _create_account(client: TestClient, email: str = "saved@example.com") -> dict:
    group_id = client.get("/api/v1/groups").json()[0]["id"]
    return client.post(
        "/api/v1/accounts/single",
        json={"email": email, "password": "secret", "recovery": "recovery@example.com", "2fa": "totp", "group_id": group_id},
    ).json()


def test_login_completion_saves_profile_for_later_reopen(monkeypatch) -> None:
    from app import service

    monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (True, 4242))
    with TestClient(app) as client:
        account = _create_account(client)
        opened = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert opened.status_code == 201
        session = opened.json()
        assert session["profile_saved"] is False
        assert session["profile_authenticated_at"] is None

        completed = client.post(f"/api/v1/sessions/{session['id']}/events", json={"event_type": "LOGIN_COMPLETED"})
        assert completed.status_code == 200
        payload = completed.json()
        assert payload["profile_saved"] is True
        assert payload["profile_authenticated_at"] is not None

        detail = client.get(f"/api/v1/accounts/{account['id']}").json()
        assert detail["profile"]["status"] == "SAVED"

        closed = client.post(f"/api/v1/sessions/{session['id']}/close")
        assert closed.status_code == 200

        reopened = client.post(f"/api/v1/accounts/{account['id']}/sessions", json={"purpose": "reuse"})
        assert reopened.status_code == 201
        reused = reopened.json()
        assert reused["profile_saved"] is True
        assert reused["profile_authenticated_at"] is not None


def test_reuse_requires_saved_profile(monkeypatch) -> None:
    from app import service

    monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (True, 4242))
    with TestClient(app) as client:
        account = _create_account(client, email="unsaved@example.com")
        opened = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        assert opened.status_code == 201
        closed = client.post(f"/api/v1/sessions/{opened.json()['id']}/close")
        assert closed.status_code == 200

        reused = client.post(f"/api/v1/accounts/{account['id']}/sessions", json={"purpose": "reuse"})
        assert reused.status_code == 422
        assert "saved" in reused.json()["detail"]


def test_invalid_session_purpose_is_rejected(monkeypatch) -> None:
    from app import service

    monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (True, 4242))
    with TestClient(app) as client:
        account = _create_account(client, email="purpose@example.com")
        response = client.post(f"/api/v1/accounts/{account['id']}/sessions", json={"purpose": "explore"})
        assert response.status_code == 422


def test_contacts_import_requires_saved_profile(monkeypatch) -> None:
    from app import service

    monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (True, 4242))
    with TestClient(app) as client:
        account = _create_account(client, email="noauth_contacts@example.com")
        resp = client.post(
            f"/api/v1/accounts/{account['id']}/contacts-import",
            json={"emails": "a@test.com\nb@test.com"},
        )
        assert resp.status_code == 422
        assert "saved" in resp.json()["detail"]


def test_contacts_import_starts_session_with_saved_profile(monkeypatch) -> None:
    from app import service

    monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (True, 4242))
    with TestClient(app) as client:
        account = _create_account(client, email="contacts_auth@example.com")
        # Simulate a saved profile via LOGIN_COMPLETED
        opened = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        session_id = opened.json()["id"]
        client.post(f"/api/v1/sessions/{session_id}/events", json={"event_type": "LOGIN_COMPLETED"})
        client.post(f"/api/v1/sessions/{session_id}/close")

        resp = client.post(
            f"/api/v1/accounts/{account['id']}/contacts-import",
            json={"emails": "alice@test.com\nbob@test.com"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["email_count"] == 2
        assert body["account_id"] == account["id"]
        assert body["status"] == "AUTHENTICATED"


def test_contact_file_library_upload_list_and_start_import(monkeypatch) -> None:
    from app import service

    monkeypatch.setattr(service, "launch_local_browser", lambda _profile_id, _session_id: (True, 4242))
    with TestClient(app) as client:
        account = _create_account(client, email="file_contacts@example.com")
        opened = client.post(f"/api/v1/accounts/{account['id']}/sessions")
        client.post(f"/api/v1/sessions/{opened.json()['id']}/events", json={"event_type": "LOGIN_COMPLETED"})
        client.post(f"/api/v1/sessions/{opened.json()['id']}/close")

        uploaded = client.post("/api/v1/contact-files", files={"file": ("partners.csv", b"Name,Email\nAlice,alice@example.com\nBob,bob@example.com\n", "text/csv")})
        assert uploaded.status_code == 201
        file_body = uploaded.json()
        assert file_body["original_filename"] == "partners.csv"
        assert file_body["contact_count"] == 2
        assert "alice@example.com" not in uploaded.text

        duplicate = client.post("/api/v1/contact-files", files={"file": ("copy.csv", b"Name,Email\nAlice,alice@example.com\nBob,bob@example.com\n", "text/csv")})
        assert duplicate.status_code == 409
        invalid = client.post("/api/v1/contact-files", files={"file": ("contacts.json", b'{"email":"alice@example.com"}', "application/json")})
        assert invalid.status_code == 422

        listed = client.get("/api/v1/contact-files")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == file_body["id"]

        started = client.post(f"/api/v1/accounts/{account['id']}/contacts-import", json={"file_id": file_body["id"]})
        assert started.status_code == 201
        assert started.json()["contact_file_id"] == file_body["id"]
        assert started.json()["email_count"] == 2
        refreshed = client.get(f"/api/v1/accounts/{account['id']}").json()
        assert refreshed["login_status"] == "AUTHENTICATED"
        assert refreshed["profile"]["status"] == "SAVED"
