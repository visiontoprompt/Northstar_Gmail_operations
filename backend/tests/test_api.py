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
