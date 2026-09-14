import os

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