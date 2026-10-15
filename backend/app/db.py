from collections.abc import Generator
from pathlib import Path

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import BACKEND_ROOT, get_settings

logger = logging.getLogger(__name__)

_authenticated_ensured = False
_proxy_inventory_ensured = False
_newsletter_agent_ensured = False
_newsletter_skill_ensured = False
_login_job_operation_ensured = False
_newsletter_signup_password_ensured = False


def _ensure_profile_authenticated_column() -> None:
    """Add browser_profiles.authenticated_at to databases created before the column existed."""
    global _authenticated_ensured
    if _authenticated_ensured:
        return
    try:
        with engine.connect() as conn:
            columns = {col["name"] for col in inspect(conn).get_columns("browser_profiles")}
            if "authenticated_at" not in columns:
                conn.execute(text("ALTER TABLE browser_profiles ADD COLUMN authenticated_at TIMESTAMP"))
                conn.commit()
    except Exception as exc:
        logger.warning("Could not ensure browser_profiles.authenticated_at column: %s", exc)
    _authenticated_ensured = True


def _ensure_proxy_inventory_payload_column() -> None:
    """Upgrade databases created before persistent proxy inventory existed."""
    global _proxy_inventory_ensured
    if _proxy_inventory_ensured:
        return
    try:
        with engine.connect() as conn:
            columns = {col["name"] for col in inspect(conn).get_columns("proxy_inventory")}
            if "encrypted_payload" not in columns:
                conn.execute(text("ALTER TABLE proxy_inventory ADD COLUMN encrypted_payload TEXT"))
                conn.commit()
    except Exception as exc:
        logger.warning("Could not ensure proxy_inventory.encrypted_payload column: %s", exc)
    _proxy_inventory_ensured = True


def _ensure_proxy_group_column() -> None:
    """Add optional named proxy-group membership to existing inventories."""
    try:
        with engine.begin() as conn:
            if not inspect(conn).has_table("proxy_inventory"):
                return
            columns = {column["name"] for column in inspect(conn).get_columns("proxy_inventory")}
            if "proxy_group" not in columns:
                conn.execute(text("ALTER TABLE proxy_inventory ADD COLUMN proxy_group VARCHAR(80)"))
    except Exception:
        logger.exception("Could not ensure proxy_inventory.proxy_group column")
        raise


def _ensure_account_tags_column() -> None:
    """Add account tags to existing registries."""
    with engine.begin() as conn:
        if not inspect(conn).has_table("gmail_accounts"):
            return
        columns = {column["name"] for column in inspect(conn).get_columns("gmail_accounts")}
        if "tags" not in columns:
            conn.execute(text("ALTER TABLE gmail_accounts ADD COLUMN tags JSON NOT NULL DEFAULT '[]'"))


def _ensure_workspace_settings_agent_columns() -> None:
    """Upgrade pre-agent databases before SQLAlchemy selects the new columns."""
    global _newsletter_agent_ensured
    if _newsletter_agent_ensured:
        return
    columns = {
        "newsletter_agent_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "newsletter_agent_provider": "VARCHAR(32) NOT NULL DEFAULT 'openrouter'",
        "newsletter_agent_model": "VARCHAR(160) NOT NULL DEFAULT 'openrouter/free'",
        "newsletter_agent_temperature": "FLOAT NOT NULL DEFAULT 0.0",
        "newsletter_agent_max_steps": "INTEGER NOT NULL DEFAULT 12",
        "newsletter_agent_timeout_seconds": "INTEGER NOT NULL DEFAULT 30",
        "newsletter_agent_shadow_mode": "BOOLEAN NOT NULL DEFAULT 1",
        "newsletter_agent_live_execution_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "newsletter_agent_learning_mode": "BOOLEAN NOT NULL DEFAULT 0",
        "newsletter_agent_primary_model": "VARCHAR(160) NOT NULL DEFAULT 'inclusionai/ling-3.0-flash-vl:free'",
        "newsletter_agent_secondary_model": "VARCHAR(160) NOT NULL DEFAULT 'nex-agi/Nex-N2.5-Mini:free'",
        "newsletter_agent_free_router_model": "VARCHAR(160) NOT NULL DEFAULT 'openrouter/free'",
        "newsletter_agent_max_ai_calls": "INTEGER NOT NULL DEFAULT 4",
        "newsletter_agent_min_confidence": "FLOAT NOT NULL DEFAULT 0.70",
        "newsletter_agent_vision_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "newsletter_agent_vision_max_calls": "INTEGER NOT NULL DEFAULT 0",
        "newsletter_agent_skill_replay_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "newsletter_agent_deterministic_resolver_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "newsletter_agent_automatic_relearning_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "newsletter_agent_chain": "JSON",
        "openrouter_api_key_encrypted": "TEXT",
        "openai_api_key_encrypted": "TEXT",
        "two_captcha_api_key_encrypted": "TEXT",
        "two_captcha_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "ovh_endpoint": "VARCHAR(16) NOT NULL DEFAULT 'ovh-eu'",
        "ovh_credentials_encrypted": "TEXT",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("workspace_settings"):
                _newsletter_agent_ensured = True
                return
            existing = {column["name"] for column in inspector.get_columns("workspace_settings")}
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE workspace_settings ADD COLUMN {name} {definition}"))
        _newsletter_agent_ensured = True
    except Exception:
        logger.exception("Could not ensure workspace_settings Newsletter Agent columns")
        raise


def _ensure_newsletter_skill_columns() -> None:
    """Upgrade skill tables created before replay lifecycle metadata existed."""
    global _newsletter_skill_ensured
    if _newsletter_skill_ensured:
        return
    columns = {
        "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
        "validation_confidence": "FLOAT NOT NULL DEFAULT 0.0",
        "last_validated_at": "TIMESTAMP",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("newsletter_automation_skills"):
                _newsletter_skill_ensured = True
                return
            existing = {column["name"] for column in inspector.get_columns("newsletter_automation_skills")}
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE newsletter_automation_skills ADD COLUMN {name} {definition}"))
        _newsletter_skill_ensured = True
    except Exception:
        logger.exception("Could not ensure newsletter skill lifecycle columns")
        raise


def _ensure_login_job_operation_columns() -> None:
    """Upgrade the shared queue for operation-specific bulk jobs."""
    global _login_job_operation_ensured
    if _login_job_operation_ensured:
        return
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("login_jobs"):
                _login_job_operation_ensured = True
                return
            existing = {column["name"] for column in inspector.get_columns("login_jobs")}
            if "operation_type" not in existing:
                conn.execute(text("ALTER TABLE login_jobs ADD COLUMN operation_type VARCHAR(32) NOT NULL DEFAULT 'login'"))
            if "operation_config" not in existing:
                conn.execute(text("ALTER TABLE login_jobs ADD COLUMN operation_config JSON NOT NULL DEFAULT '{}'"))
        _login_job_operation_ensured = True
    except Exception:
        logger.exception("Could not ensure login job operation columns")
        raise


def _ensure_newsletter_signup_password_column() -> None:
    """Keep existing installations readable before subscription ORM queries."""
    global _newsletter_signup_password_ensured
    if _newsletter_signup_password_ensured:
        return
    with engine.begin() as conn:
        inspector = inspect(conn)
        if inspector.has_table("newsletter_subscriptions"):
            existing = {column["name"] for column in inspector.get_columns("newsletter_subscriptions")}
            if "signup_password_encrypted" not in existing:
                conn.execute(text("ALTER TABLE newsletter_subscriptions ADD COLUMN signup_password_encrypted TEXT"))
    _newsletter_signup_password_ensured = True


class Base(DeclarativeBase):
    pass


settings = get_settings()


def _resolve_database_url(database_url: str) -> str:
    """Anchor relative SQLite files to the backend project, not the cwd.

    The API is sometimes started from the repository root and sometimes from
    ``backend/``.  A relative SQLite URL would silently create two separate
    databases in those cases, making accounts and registry records appear to
    disappear after a restart.
    """
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return database_url
    raw_path = database_url[len(prefix):]
    if raw_path in {":memory:", ""} or raw_path.startswith("file:"):
        return database_url
    path = Path(raw_path)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return f"{prefix}{path.resolve()}"


database_url = _resolve_database_url(settings.database_url)
if settings.database_url.startswith("sqlite"):
    if not database_url.endswith(":memory:"):
        Path(database_url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    _ensure_profile_authenticated_column()
    _ensure_proxy_inventory_payload_column()
    _ensure_proxy_group_column()
    _ensure_account_tags_column()
    _ensure_workspace_settings_agent_columns()
    _ensure_newsletter_skill_columns()
    _ensure_login_job_operation_columns()
    _ensure_newsletter_signup_password_column()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_production_schema() -> None:
    """Fail closed when production migrations have not been applied to this release."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    if not inspect(engine).has_table("alembic_version"):
        raise RuntimeError("Database migrations are missing; run `alembic -c alembic.ini upgrade head` before starting the production service")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations" / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if revision != head:
        raise RuntimeError(f"Database revision {revision!r} does not match release head {head!r}; run `alembic -c alembic.ini upgrade head`")
