from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Gmail Operations API"
    environment: str = "development"
    database_url: str = "sqlite:///./data/gmail_ops.db"
    secret_key: str = "dev-only-change-me-before-production"
    cors_origins: str = "http://localhost:5173"
    demo_org_id: str = "org_demo"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment.lower() == "production" and settings.secret_key.startswith("dev-only"):
        raise RuntimeError("SECRET_KEY must be configured outside development")
    return settings
