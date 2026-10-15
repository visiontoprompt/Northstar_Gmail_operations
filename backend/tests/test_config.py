import pytest

from app.config import get_settings


def test_production_rejects_sqlite_even_with_other_required_settings(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/prod.db")
    monkeypatch.setenv("SECRET_KEY", "production-secret-key")
    monkeypatch.setenv("ADMIN_API_TOKEN", "break-glass-token")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ALLOW_INITIAL_ADMIN_SETUP", "false")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        get_settings.__wrapped__()
