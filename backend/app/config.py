from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Gmail Operations API"
    environment: str = "development"
    database_url: str = "sqlite:///./data/gmail_ops.db"
    secret_key: str = "dev-only-change-me-before-production"
    admin_api_token: str | None = None
    cors_origins: str = "http://localhost:5173"
    demo_org_id: str = "org_demo"
    browser_launch_enabled: bool = False
    browser_executable: str = "google-chrome"
    profile_root: str = "data/browser-profiles"
    browser_observer_enabled: bool = True
    browser_cdp_port: int = 0
    browser_observe_interval: float = 1.0
    redis_url: str = "redis://localhost:6379/0"
    worker_enabled: bool = False
    worker_poll_interval: float = 5.0
    worker_heartbeat_ttl: int = 30
    worker_heartbeat_interval: float = 10.0
    worker_stale_seconds: int = 90
    worker_lock_ttl: int = 120
    worker_retry_limit: int = 3
    worker_max_active_profiles: int = 50
    worker_release_on_complete: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment.lower() == "production" and settings.secret_key.startswith("dev-only"):
        raise RuntimeError("SECRET_KEY must be configured outside development")
    if settings.environment.lower() == "production" and not settings.admin_api_token:
        raise RuntimeError("ADMIN_API_TOKEN must be configured outside development")
    return settings
