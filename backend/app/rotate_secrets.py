"""Re-encrypt all persisted Fernet values with the current SECRET_KEY.

Run during a maintenance window after setting SECRET_KEY to the new key and
SECRET_KEY_PREVIOUS to the old one. The command never prints secret material.
"""
from sqlalchemy import inspect, select, update

from .config import get_settings
from .db import Base, SessionLocal, engine
from .security import SecretCipher
import app.models  # noqa: F401

_ENCRYPTED_COLUMNS = {"encrypted_payload", "encrypted_credentials", "recovery_pool_encrypted", "signup_password_encrypted"}


def main() -> int:
    settings = get_settings()
    if not settings.secret_key_previous or settings.secret_key_previous == settings.secret_key:
        raise SystemExit("Set a distinct SECRET_KEY_PREVIOUS before rotating persisted secrets.")
    cipher = SecretCipher()
    current_only = SecretCipher._derive(settings.secret_key)
    inspector = inspect(engine)
    rotated = 0
    with SessionLocal() as db:
        try:
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                columns = [column for column in table.columns if column.name.endswith("_encrypted") or column.name in _ENCRYPTED_COLUMNS]
                primary_keys = list(table.primary_key.columns)
                if not columns or not primary_keys:
                    continue
                for row in db.execute(select(table)).mappings():
                    key = {column.name: row[column.name] for column in primary_keys}
                    values = {}
                    for column in columns:
                        encrypted = row[column.name]
                        if encrypted:
                            values[column.name] = cipher.rotate(encrypted)
                    if values:
                        db.execute(update(table).where(*[table.c[name] == value for name, value in key.items()]).values(**values))
                        rotated += len(values)
            db.commit()
            # Verify every persisted value can now be read with the new key alone.
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                columns = [column for column in table.columns if column.name.endswith("_encrypted") or column.name in _ENCRYPTED_COLUMNS]
                if not columns:
                    continue
                for row in db.execute(select(*columns)).all():
                    for value in row:
                        if value:
                            current_only.decrypt(value.encode())
        except Exception:
            db.rollback()
            raise
    print(f"Re-encrypted and verified {rotated} stored secret values with the current key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
