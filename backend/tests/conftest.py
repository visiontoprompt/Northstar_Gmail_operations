import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_gmail_ops.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-without-credential-material")
os.environ.setdefault("REDIS_URL", "fakeredis://")
os.environ.setdefault("WORKER_ENABLED", "false")
os.environ.setdefault("WORKER_POLL_INTERVAL", "0.1")
os.environ.setdefault("WORKER_HEARTBEAT_INTERVAL", "0.2")
os.environ.setdefault("WORKER_HEARTBEAT_TTL", "5")
os.environ.setdefault("WORKER_STALE_SECONDS", "30")
os.environ.setdefault("WORKER_LOCK_TTL", "8")
os.environ.setdefault("WORKER_RETRY_LIMIT", "2")
os.environ.setdefault("WORKER_MAX_ACTIVE_PROFILES", "50")


@pytest.fixture(autouse=True)
def fake_browser_launch(monkeypatch):
    monkeypatch.setattr("app.service.launch_local_browser", lambda profile_id, session_id: (True, 4242))
    yield