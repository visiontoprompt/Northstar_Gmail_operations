from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_name: str = "Gmail Operations API"
    environment: str = "development"
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'data' / 'gmail_ops.db'}"
    secret_key: str = "dev-only-change-me-before-production"
    # Keep the prior key here during a rotation window; writes always use the
    # current key while reads accept both.
    secret_key_previous: str | None = None
    admin_api_token: str | None = None
    auth_required: bool = False
    allow_initial_admin_setup: bool = True
    bootstrap_admin_email: str = "admin@northstar.local"
    session_ttl_hours: int = 12
    cors_origins: str = "http://localhost:5173"
    demo_org_id: str = "org_demo"
    browser_launch_enabled: bool = False
    browser_desktop_enabled: bool = False
    browser_executable: str = "google-chrome"
    profile_root: str = str(PROJECT_ROOT / "data" / "browser-profiles")
    # Local mode launches each account's saved profile as a normal Chrome window
    # via Playwright's persistent context; the observer drives sign-in and the
    # saved profile/cookies persist for future no-login sessions. These flags
    # are opt-in: dev.sh enables them all automatically.
    browser_observer_enabled: bool = False
    browser_auto_login: bool = False
    browser_observe_interval: float = 1.0
    browser_observe_settle_seconds: float = 5.0
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
    proxy_test_url: str = "https://ipwho.is/"
    proxy_test_timeout_seconds: float = 8.0
    openrouter_api_key: str | None = None
    openrouter_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = 8.0
    newsletter_agent_fallback_enabled: bool = False
    newsletter_agent_max_calls: int = 3
    # Optional browser-use resolver. It is tried once per session, and only
    # after the deterministic selector lookup, a replayed learned skill, and
    # the configured agent chain have all failed. On by default, but inert
    # unless the browser-use extra is installed *and* an OpenAI key is
    # configured; it also still honours the agent's shadow-mode and
    # live-execution gates.
    newsletter_browser_use_enabled: bool = True
    newsletter_browser_use_model: str = "gpt-4o-mini"
    newsletter_browser_use_timeout_seconds: float = 25.0
    # Browser-backed re-reads of challenge-protected public pages during
    # discovery. Each one spawns a real browser, so this stays small.
    newsletter_discovery_stealth_max_fetches: int = 2
    # Signup runs with no saved form profile resolve the live page as they type
    # instead of waiting for an inspection pass or a configured agent. It makes
    # no provider call, so it is on by default; switching it off restores the
    # old "manual review unless a profile or the agent is ready" behaviour.
    newsletter_live_form_enabled: bool = True
    ovh_proxy_recovery_enabled: bool = True
    ovh_proxy_recovery_project_id: str = "239fa25938524e2f87575cc424a2b6fc"
    ovh_proxy_recovery_instance_id: str = "e998e455-3c20-40f3-bd9b-63d74a57d4de"
    ovh_proxy_recovery_region: str = "US-EAST-VA-1"
    ovh_proxy_recovery_port: int = 18080
    ovh_proxy_recovery_username: str | None = None
    ovh_proxy_recovery_password: str | None = None
    ovh_proxy_recovery_ip_check_url: str = "https://api4.ipify.org"
    ovh_proxy_recovery_probe_attempts: int = 3
    ovh_proxy_recovery_readiness_timeout_seconds: int = 600
    ovh_proxy_recovery_network_grace_seconds: int = 90
    ovh_proxy_recovery_poll_seconds: int = 5
    openstack_cloud: str = "northstar-ovh-us"

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
    if settings.environment.lower() == "production" and settings.allow_initial_admin_setup:
        raise RuntimeError("ALLOW_INITIAL_ADMIN_SETUP must be disabled outside development")
    if settings.environment.lower() == "production" and settings.database_url.lower().startswith("sqlite"):
        raise RuntimeError("DATABASE_URL must use PostgreSQL in production")
    if settings.environment.lower() == "production" and not settings.auth_required:
        raise RuntimeError("AUTH_REQUIRED must be enabled in production")
    return settings
