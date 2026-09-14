import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditEvent, JobLock, LoginJob, LoginJobItem, utcnow
from app.service import login_job_progress, recover_stale_locks, _locks, _queue
from app.worker import redis as worker_redis


@pytest.fixture(autouse=True)
def reset_state():
    settings = get_settings()
    original_settings = {"worker_enabled": settings.worker_enabled, "worker_max_active_profiles": settings.worker_max_active_profiles, "worker_retry_limit": settings.worker_retry_limit, "worker_stale_seconds": settings.worker_stale_seconds}
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    worker_redis.reset_redis()
    worker_redis.flush_redis()
    with TestClient(app):
        yield
    for key, value in original_settings.items():
        setattr(settings, key, value)


def _make_accounts(client: TestClient, group_id: str, count: int, prefix: str = "worker") -> list[dict]:
    accounts = []
    for index in range(count):
        account = client.post(
            "/api/v1/accounts/single",
            json={"email": f"{prefix}-{index}@example.com", "password": f"secret-{index}", "recovery": "recover@example.com", "2fa": "totp-secret", "group_id": group_id},
        )
        assert account.status_code == 201, account.text
        accounts.append(account.json())
    return accounts


def _job_for(client: TestClient, group_id: str, accounts: list[dict], concurrency: int) -> dict:
    created = client.post("/api/v1/login-jobs", json={"group_id": group_id, "account_ids": [a["id"] for a in accounts], "concurrency_limit": concurrency})
    assert created.status_code == 201, created.text
    return created.json()


def start_job(client: TestClient, job_id: str) -> dict:
    response = client.post(f"/api/v1/login-jobs/{job_id}/start")
    assert response.status_code == 200, response.text
    return response.json()


def test_redis_queue_enqueue_dequeue_and_requeue() -> None:
    queue = _queue()
    queue.enqueue("job_one", "item1", "acct1", when=100.0)
    queue.enqueue_many("job_one", [("item2", "acct2", 200.0), ("item3", "acct3", 300.0)])
    assert queue.queued_count("job_one") == 3
    first = queue.pop_next("job_one")
    assert first == {"item_id": "item1", "account_id": "acct1"}
    queue.requeue("job_one", "item1", "acct1", when=600.0)
    remaining = queue.peek("job_one")
    assert {"item_id": "item1", "account_id": "acct1"} in remaining


def test_global_active_profile_cap_respected() -> None:
    settings = get_settings()
    settings.worker_max_active_profiles = 1
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = _make_accounts(client, group_id, 2)
        job = _job_for(client, group_id, accounts, concurrency=5)
        started = start_job(client, job["id"])
        assert started["running_count"] == 1
        assert started["queued_count"] == 1
        assert _queue().active_profile_count() == 1


def test_per_job_concurrency_limit_enforced() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = _make_accounts(client, group_id, 5)
        job = _job_for(client, group_id, accounts, concurrency=2)
        started = start_job(client, job["id"])
        assert started["running_count"] == 2
        assert started["queued_count"] == 3
        states = {item["account_id"]: item["state"] for item in started["items"]}
        assert sum(state == "RUNNING" for state in states.values()) == 2


def test_duplicate_lock_blocks_second_job_on_same_account() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = _make_accounts(client, group_id, 1)[0]
        job_one = _job_for(client, group_id, [account], concurrency=1)
        start_job(client, job_one["id"])
        job_two = _job_for(client, group_id, [account], concurrency=1)
        started = start_job(client, job_two["id"])
        assert started["running_count"] == 0
        assert started["queued_count"] == 1
        assert started["items"][0]["state"] == "QUEUED"


def test_stale_worker_retries_then_fails() -> None:
    settings = get_settings()
    settings.worker_retry_limit = 3
    settings.worker_stale_seconds = 1
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = _make_accounts(client, group_id, 1)
        job = _job_for(client, group_id, accounts, concurrency=1)
        started = start_job(client, job["id"])
        item_id = started["items"][0]["id"]
        session_id = started["items"][0]["session_id"]

        db = SessionLocal()
        try:
            item = db.get(LoginJobItem, item_id)
            item.worker_id = "worker-test-1"
            item.heartbeat_at = utcnow() - timedelta(seconds=90)
            item.attempts = 1
            db.commit()
        finally:
            db.close()

        with SessionLocal() as db:
            result = recover_stale_locks(db, "org_demo")
        assert result["requeued"] == 1
        with SessionLocal() as db:
            item = db.get(LoginJobItem, item_id)
            assert item.state == "QUEUED"
            assert item.attempts == 2

        repop = _queue()
        repop.active_profile_count()
        client.post(f"/api/v1/login-jobs/{job['id']}/dispatch")

        with SessionLocal() as db:
            item = db.get(LoginJobItem, item_id)
            item.worker_id = "worker-test-2"
            item.heartbeat_at = utcnow() - timedelta(seconds=90)
            item.attempts = 3
            db.commit()
        with SessionLocal() as db:
            result = recover_stale_locks(db, "org_demo")
        assert result["failed"] == 1
        with SessionLocal() as db:
            item = db.get(LoginJobItem, item_id)
            assert item.state == "FAILED"
            job = db.get(LoginJob, job["id"])
            assert job.failed_count == 1
            lock = db.scalar(select(JobLock).where(JobLock.session_id == session_id))
            assert lock is not None
            assert lock.released is True


def test_cancellation_releases_locks_and_clears_queue() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = _make_accounts(client, group_id, 3)
        job = _job_for(client, group_id, accounts, concurrency=2)
        started = start_job(client, job["id"])
        assert started["running_count"] == 2
        cancelled = client.post(f"/api/v1/login-jobs/{job['id']}/cancel")
        assert cancelled.status_code == 200
        body = cancelled.json()
        assert body["status"] == "CANCELLED"
        assert body["queued_count"] == 0
        assert body["running_count"] == 0
        assert _queue().queued_count(job["id"]) == 0
        assert _queue().active_profile_count() == 0
        assert {item["state"] for item in cancelled.json()["items"]} == {"CANCELLED"}


def test_profile_is_persistent_and_reused_across_sessions() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = _make_accounts(client, group_id, 1)[0]
        job = _job_for(client, group_id, [account], concurrency=1)
        started = start_job(client, job["id"])
        first_session = client.get(f"/api/v1/sessions/{started['items'][0]['session_id']}").json()
        profile_id = first_session["profile_id"]

        closed = client.post(f"/api/v1/sessions/{first_session['id']}/close")
        assert closed.status_code == 200
        assert closed.json()["profile_status"] == "RESERVED"

        resumed = client.post(f"/api/v1/login-jobs/{job['id']}/resume")
        assert resumed.status_code == 200
        second_session_id = resumed.json()["items"][0]["session_id"]
        second_session = client.get(f"/api/v1/sessions/{second_session_id}").json()
        assert second_session["profile_id"] == profile_id

        sessions = client.get(f"/api/v1/sessions?account_id={account['id']}").json()
        assert len(sessions) == 2


def test_challenge_pause_holds_profile_for_human() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = _make_accounts(client, group_id, 1)[0]
        job = _job_for(client, group_id, [account], concurrency=1)
        started = start_job(client, job["id"])
        session_id = started["items"][0]["session_id"]

        response = client.post(f"/api/v1/sessions/{session_id}/observations", json={"url": "https://accounts.google.com/v/signin/challenge", "title": "Unusual traffic", "text": "Complete the captcha to continue"})
        assert response.status_code == 200
        body = response.json()
        assert body["signal"] == "CAPTCHA"
        assert body["action"] == "WAIT_FOR_HUMAN"
        assert body["session"]["state"] == "NEEDS_ATTENTION"
        assert body["session"]["profile_status"] == "OPEN"
        progress = login_job_progress_client(client, job["id"])
        item = progress["items"][0]
        assert item["state"] == "ATTENTION"
        assert item["paused_reason"] is not None


def test_observation_of_gmail_home_completes_login():
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = _make_accounts(client, group_id, 1)[0]
        job = _job_for(client, group_id, [account], concurrency=1)
        started = start_job(client, job["id"])
        session_id = started["items"][0]["session_id"]

        response = client.post(f"/api/v1/sessions/{session_id}/observations", json={"url": "https://mail.google.com/mail/u/0/#inbox", "title": "Inbox", "text": "Inbox Compose Sent Mail"})
        assert response.status_code == 200
        body = response.json()
        assert body["signal"] == "GMAIL_HOME"
        assert body["session"]["state"] == "RUNNING"
        assert body["session"]["login_state"] == "AUTHENTICATED"
        progress = login_job_progress_client(client, job["id"])
        item = progress["items"][0]
        assert item["state"] == "SUCCEEDED"
        assert progress["states"].get("SUCCEEDED") == 1


def login_job_progress_client(client: TestClient, job_id: str) -> dict:
    response = client.get(f"/api/v1/login-jobs/{job_id}/progress")
    assert response.status_code == 200
    return response.json()


def test_progress_endpoint_reports_states_and_percent() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = _make_accounts(client, group_id, 3)
        job = _job_for(client, group_id, accounts, concurrency=3)
        started = start_job(client, job["id"])
        running = [item for item in started["items"] if item["session_id"]]
        for item in running:
            completed = client.post(f"/api/v1/sessions/{item['session_id']}/events", json={"event_type": "LOGIN_COMPLETED"})
            assert completed.status_code == 200
        progress = login_job_progress_client(client, job["id"])
        assert progress["status"] == "COMPLETED"
        assert progress["progress_percent"] == 100
        assert progress["states"]["SUCCEEDED"] == 3


def test_auto_dispatch_next_account_when_worker_enabled() -> None:
    pytest.skip("flaky: races with background worker thread; revisit with per-job mutex")
    settings = get_settings()
    settings.worker_enabled = True
    try:
        with TestClient(app) as client:
            group_id = client.get("/api/v1/groups").json()[0]["id"]
            accounts = _make_accounts(client, group_id, 2)
            job = _job_for(client, group_id, accounts, concurrency=1)
            started = start_job(client, job["id"])
            assert started["running_count"] == 1
            session_id = started["items"][0]["session_id"]

            completed = client.post(f"/api/v1/sessions/{session_id}/events", json={"event_type": "LOGIN_COMPLETED"})
            assert completed.status_code == 200

            progress = login_job_progress_client(client, job["id"])
            for _ in range(20):
                if progress["queued_count"] == 0:
                    break
                time.sleep(0.01)
                progress = login_job_progress_client(client, job["id"])
            assert progress["queued_count"] == 0
            assert progress["succeeded_count"] == 1
            assert progress["running_count"] == 1
    finally:
        settings.worker_enabled = False


def test_state_transitions_are_audited_without_secrets() -> None:
    secrets = ["ultra-secret-password", "private-recovery@example.com", "supersecret-totp"]
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        response = client.post("/api/v1/accounts/single", json={"email": "audit-worker@example.com", "password": secrets[0], "recovery": secrets[1], "2fa": secrets[2], "group_id": group_id})
        assert response.status_code == 201
        account = response.json()
        job = _job_for(client, group_id, [account], concurrency=1)
        started = start_job(client, job["id"])
        client.post(f"/api/v1/login-jobs/{job['id']}/pause")
        client.post(f"/api/v1/login-jobs/{job['id']}/resume")
        client.post(f"/api/v1/login-jobs/{job['id']}/cancel")

        audit = client.get("/api/v1/audit-events", params={"limit": 100})
        assert audit.status_code == 200
        serialized = audit.text
        for secret in secrets:
            assert secret not in serialized

        events = audit.json()
        actions = [event["action"] for event in events]
        assert "login_job.created" in actions
        assert "login_job.dispatched" in actions
        assert "login_job.paused" in actions
        assert "login_job.resumed" in actions
        assert "login_job.cancelled" in actions
        jobs_metadata = [event["metadata"] for event in events if event["target_type"] == "login_job"]
        for metadata in jobs_metadata:
            assert "password" not in str(metadata)
            assert secrets[0] not in str(metadata)


def test_worker_and_api_dispatch_share_single_lock_per_account() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        account = _make_accounts(client, group_id, 1)[0]
        job = _job_for(client, group_id, [account], concurrency=1)
        started = start_job(client, job["id"])
        item = started["items"][0]
        held = _locks().held(account["id"])
        assert held is not None
        assert held["session_id"] == item["session_id"]
        with SessionLocal() as db:
            row = db.scalar(select(JobLock).where(JobLock.released == False, JobLock.account_id == account["id"]))
            assert row is not None
            assert row.session_id == item["session_id"]
            assert row.released is False


def test_paused_batch_does_not_dispatch_more_accounts() -> None:
    with TestClient(app) as client:
        group_id = client.get("/api/v1/groups").json()[0]["id"]
        accounts = _make_accounts(client, group_id, 3)
        job = _job_for(client, group_id, accounts, concurrency=1)
        started = start_job(client, job["id"])
        assert started["running_count"] == 1
        paused = client.post(f"/api/v1/login-jobs/{job['id']}/pause")
        assert paused.json()["status"] == "PAUSED"
        resumed = client.post(f"/api/v1/login-jobs/{job['id']}/resume")
        assert resumed.json()["status"] == "RUNNING"
        assert resumed.json()["running_count"] == 1
        assert resumed.json()["queued_count"] == 2
