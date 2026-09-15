from collections.abc import Generator
from pathlib import Path

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)

_authenticated_ensured = False


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


class Base(DeclarativeBase):
    pass


settings = get_settings()
if settings.database_url.startswith("sqlite"):
    Path("data").mkdir(exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    _ensure_profile_authenticated_column()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
