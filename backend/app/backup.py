"""Encrypted, portable workspace backups.

The archive contains every SQLAlchemy application table, the complete browser
profile root, and a private copy of runtime/deployment secrets. The outer file
is encrypted with a user-supplied passphrase so it remains recoverable when the
server's SECRET_KEY is unavailable.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import Table, delete, insert, select
from sqlalchemy.orm import Session

from .config import PROJECT_ROOT, get_settings
from .db import Base, engine
from . import models as _models  # noqa: F401 — ensure all mapped tables are registered.
from .models import AuditEvent

logger = logging.getLogger(__name__)
MAGIC = b"NSTARBAK"
VERSION = 1
HEADER_SIZE = len(MAGIC) + 1 + 16 + 12
CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024
MIN_PASSPHRASE_LENGTH = 12


class BackupError(ValueError):
    pass


def _passphrase_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise BackupError(f"Use a backup passphrase with at least {MIN_PASSPHRASE_LENGTH} characters.")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _encrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _passphrase_key(passphrase, salt)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    with source.open("rb") as src, destination.open("wb") as dst:
        dst.write(MAGIC + bytes([VERSION]) + salt + nonce)
        while chunk := src.read(CHUNK_SIZE):
            dst.write(encryptor.update(chunk))
        dst.write(encryptor.finalize())
        dst.write(encryptor.tag)


def _encrypt_archive(content: Path, profile_root: Path, destination: Path, passphrase: str) -> None:
    """Stream tar contents directly into AES-GCM to avoid a plaintext tar copy."""
    salt, nonce = os.urandom(16), os.urandom(12)
    encryptor = Cipher(algorithms.AES(_passphrase_key(passphrase, salt)), modes.GCM(nonce)).encryptor()
    with destination.open("wb") as output:
        output.write(MAGIC + bytes([VERSION]) + salt + nonce)

        class EncryptedSink:
            written = 0

            def write(self, data: bytes) -> int:
                output.write(encryptor.update(data))
                self.written += len(data)
                return len(data)

            def tell(self) -> int:
                return self.written

            def flush(self) -> None:
                output.flush()

        sink = EncryptedSink()
        with tarfile.open(fileobj=sink, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            archive.add(content, arcname="northstar", recursive=True, filter=lambda entry: None if entry.issym() or entry.islnk() else entry)
            if profile_root.exists():
                archive.add(profile_root, arcname="northstar/profiles", recursive=True, filter=lambda entry: None if entry.issym() or entry.islnk() else entry)
            else:
                empty = tarfile.TarInfo("northstar/profiles")
                empty.type = tarfile.DIRTYPE
                archive.addfile(empty)
        output.write(encryptor.finalize())
        output.write(encryptor.tag)


def _decrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    with source.open("rb") as src:
        header = src.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE or header[:len(MAGIC)] != MAGIC or header[len(MAGIC)] != VERSION:
            raise BackupError("This file is not a supported Northstar backup.")
        salt_start = len(MAGIC) + 1
        salt, nonce = header[salt_start:salt_start + 16], header[salt_start + 16:salt_start + 28]
        size = source.stat().st_size
        if size < HEADER_SIZE + 16:
            raise BackupError("The backup file is incomplete.")
        ciphertext_size = size - HEADER_SIZE - 16
        src.seek(size - 16)
        tag = src.read(16)
        src.seek(HEADER_SIZE)
        decryptor = Cipher(algorithms.AES(_passphrase_key(passphrase, salt)), modes.GCM(nonce, tag)).decryptor()
        remaining = ciphertext_size
        try:
            with destination.open("wb") as dst:
                while remaining:
                    chunk = src.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupError("The backup file is incomplete.")
                    remaining -= len(chunk)
                    dst.write(decryptor.update(chunk))
                dst.write(decryptor.finalize())
        except Exception:
            destination.unlink(missing_ok=True)
            raise BackupError("The passphrase is incorrect or the backup is damaged.") from None


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$type": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$type": "decimal", "value": str(value)}
    if isinstance(value, bytes):
        return {"$type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _from_json_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_from_json_value(item) for item in value]
    if isinstance(value, dict):
        kind = value.get("$type")
        raw = value.get("value")
        if kind == "datetime":
            return datetime.fromisoformat(raw)
        if kind == "date":
            return date.fromisoformat(raw)
        if kind == "decimal":
            return Decimal(raw)
        if kind == "bytes":
            return base64.b64decode(raw, validate=True)
        return {key: _from_json_value(item) for key, item in value.items()}
    return value


def _runtime_files(staging: Path) -> dict[str, Any]:
    settings = get_settings()
    runtime = {
        key: value for key, value in settings.model_dump().items()
        if any(word in key.lower() for word in ("secret", "key", "token", "password", "database_url"))
        and value is not None
    }
    (staging / "deployment-config").mkdir(parents=True, exist_ok=True)
    (staging / "deployment-config" / "runtime-secrets.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    copied: list[str] = ["runtime-secrets.json"]
    candidates: list[tuple[str, Path]] = []
    env_file = os.getenv("NORTHSTAR_ENV_FILE")
    if env_file:
        candidates.append(("northstar.env", Path(env_file)))
    candidates.extend([
        ("northstar.env", Path("/etc/northstar/northstar.env")),
        ("backend.env", PROJECT_ROOT / "backend" / ".env"),
        ("project.env", PROJECT_ROOT / ".env"),
    ])
    cloud_file = os.getenv("OS_CLIENT_CONFIG_FILE")
    if cloud_file:
        candidates.append(("clouds.yaml", Path(cloud_file)))
    seen: set[Path] = set()
    for name, path in candidates:
        try:
            canonical = path.resolve()
            if canonical in seen:
                continue
            seen.add(canonical)
            if path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
                shutil.copyfile(path, staging / "deployment-config" / name)
                copied.append(name)
        except OSError:
            logger.warning("Backup could not read one configured deployment file: %s", name)
    return {"included": copied}


def create_backup(passphrase: str, *, persistent: bool = False) -> Path:
    """Create and return an encrypted backup file in a temporary directory."""
    _passphrase_key(passphrase, os.urandom(16))  # validate before doing expensive work
    settings = get_settings()
    parent = Path(get_settings().profile_root).resolve().parent
    tmp_root = Path(tempfile.mkdtemp(prefix="northstar-backup-", dir=parent if persistent else None))
    encrypted = tmp_root / "northstar-backup.nstar"
    try:
        content = tmp_root / "content"
        (content / "database").mkdir(parents=True)
        metadata = {
            "format": "northstar-backup", "version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database_dialect": engine.dialect.name,
            "tables": [], "runtime_config": _runtime_files(content),
        }
        tables = list(Base.metadata.sorted_tables)
        with engine.connect() as connection:
            for table in tables:
                target = content / "database" / f"{table.name}.jsonl"
                count = 0
                with target.open("w", encoding="utf-8") as output:
                    for row in connection.execute(select(table)).mappings():
                        output.write(json.dumps(_json_value(dict(row)), separators=(",", ":"), ensure_ascii=False) + "\n")
                        count += 1
                metadata["tables"].append({"name": table.name, "rows": count})
        (content / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        profile_root = Path(settings.profile_root).resolve()
        _encrypt_archive(content, profile_root, encrypted, passphrase)
        if persistent:
            backup_dir = parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            final_path = backup_dir / f"restore-safety-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}.nstar"
            encrypted.replace(final_path)
            final_path.chmod(0o600)
            shutil.rmtree(tmp_root, ignore_errors=True)
            return final_path
        return encrypted
    except Exception:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise


def _safe_extract_profiles(archive: tarfile.TarFile, target: Path) -> None:
    prefix = "northstar/profiles/"
    for member in archive.getmembers():
        if not member.name.startswith(prefix):
            continue
        output = _profile_output_path(member, target)
        if member.isdir():
            output.mkdir(parents=True, exist_ok=True)
            output.chmod(0o700)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise BackupError("The backup contains an unreadable browser-profile file.")
            with source, output.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=CHUNK_SIZE)
            output.chmod(0o600)


def _profile_output_path(member: tarfile.TarInfo, target: Path) -> Path:
    prefix = "northstar/profiles/"
    relative = PurePosixPath(member.name[len(prefix):])
    if relative.is_absolute() or ".." in relative.parts or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
        raise BackupError("The backup contains an unsafe browser-profile entry.")
    output = (target / Path(*relative.parts)).resolve()
    if not output.is_relative_to(target.resolve()):
        raise BackupError("The backup contains an unsafe browser-profile path.")
    return output


def _validate_database_payload(archive: tarfile.TarFile) -> tuple[dict[str, Any], dict[str, Table]]:
    try:
        metadata_file = archive.extractfile("northstar/metadata.json")
        metadata = json.load(metadata_file) if metadata_file else None
    except Exception as exc:
        raise BackupError("The backup metadata is missing or invalid.") from exc
    if not isinstance(metadata, dict) or metadata.get("format") != "northstar-backup" or metadata.get("version") != VERSION:
        raise BackupError("This backup format is not supported by this app version.")
    known = {table.name: table for table in Base.metadata.sorted_tables}
    metadata_tables = {item.get("name") for item in metadata.get("tables", []) if isinstance(item, dict)}
    if metadata_tables != set(known):
        raise BackupError("The backup is incomplete or was created by an incompatible app version.")
    for item in metadata.get("tables", []):
        if not isinstance(item, dict) or item.get("name") not in known:
            raise BackupError("The backup contains data for an unknown database table.")
        member = archive.getmember(f"northstar/database/{item['name']}.jsonl")
        if not member.isfile():
            raise BackupError("A database table file is missing from the backup.")
    return metadata, known


def validate_backup_file(upload: Path, passphrase: str) -> dict[str, Any]:
    """Authenticate and check archive structure before any live-state snapshot or mutation."""
    with tempfile.TemporaryDirectory(prefix="northstar-verify-") as temporary:
        plain = Path(temporary) / "payload.tar"
        _decrypt_file(upload, plain, passphrase)
        try:
            with tarfile.open(plain, "r:") as archive:
                metadata, _ = _validate_database_payload(archive)
                for member in archive.getmembers():
                    if member.name.startswith("northstar/profiles/"):
                        _profile_output_path(member, Path(temporary) / "profiles")
                return metadata
        except (tarfile.TarError, KeyError) as exc:
            raise BackupError("The backup archive is incomplete or damaged.") from exc


def restore_backup(upload: Path, passphrase: str, db: Session, *, actor_id: str, safety_backup_name: str) -> dict[str, Any]:
    """Verify, then replace application DB/profile data from an archive."""
    staging_root = Path(tempfile.mkdtemp(prefix="northstar-restore-"))
    plain = staging_root / "payload.tar"
    profile_stage: Path | None = None
    live_profile = Path(get_settings().profile_root).resolve()
    displaced_profile = live_profile.with_name(f".{live_profile.name}.before-restore-{uuid.uuid4().hex}")
    profile_swapped = False
    config_target: Path | None = None
    try:
        _decrypt_file(upload, plain, passphrase)
        with tarfile.open(plain, "r:") as archive:
            metadata, tables = _validate_database_payload(archive)
            profile_stage = live_profile.with_name(f".{live_profile.name}.restore-{uuid.uuid4().hex}")
            profile_stage.mkdir(parents=True, exist_ok=False)
            _safe_extract_profiles(archive, profile_stage)
            # Save a secure copy of deployment-managed credentials for recovery.
            # The process does not overwrite /etc files or change its live keys.
            config_target = Path(get_settings().profile_root).resolve().parent / f"restored-deployment-config-{uuid.uuid4().hex[:8]}"
            config_target.mkdir(parents=True, exist_ok=True, mode=0o700)
            for member in archive.getmembers():
                if member.name.startswith("northstar/deployment-config/") and member.isfile():
                    name = PurePosixPath(member.name).name
                    source = archive.extractfile(member)
                    if source:
                        with source, (config_target / name).open("wb") as out:
                            shutil.copyfileobj(source, out)
                        (config_target / name).chmod(0o600)

            # The current transaction rolls back all DB changes if any table fails.
            for table in reversed(list(tables.values())):
                db.execute(delete(table))
            for table in tables.values():
                source = archive.extractfile(f"northstar/database/{table.name}.jsonl")
                if source is None:
                    continue
                with source:
                    for line in source:
                        if not line.strip():
                            continue
                        row = _from_json_value(json.loads(line))
                        allowed = {column.name for column in table.columns}
                        if not isinstance(row, dict) or not set(row).issubset(allowed):
                            raise BackupError(f"The backup has invalid data for table {table.name}.")
                        db.execute(insert(table).values(**row))
            db.add(AuditEvent(
                id=f"audit_{uuid.uuid4().hex[:18]}",
                organization_id=get_settings().demo_org_id,
                actor_id=actor_id,
                action="workspace.backup.restored",
                target_type="workspace_backup",
                target_id="upload",
                result="success",
                metadata_json={"table_count": len(tables), "safety_backup": safety_backup_name},
            ))
            db.flush()
            live_profile.parent.mkdir(parents=True, exist_ok=True)
            if live_profile.exists():
                live_profile.rename(displaced_profile)
            profile_stage.rename(live_profile)
            profile_swapped = True
            db.commit()
            shutil.rmtree(displaced_profile, ignore_errors=True)
            return {"restored_at": datetime.now(timezone.utc).isoformat(), "table_count": len(tables), "tables": metadata["tables"], "profile_root_restored": True, "deployment_config_staged": str(config_target)}
    except Exception:
        db.rollback()
        if profile_swapped:
            shutil.rmtree(live_profile, ignore_errors=True)
        if displaced_profile.exists():
            displaced_profile.rename(live_profile)
        if config_target and config_target.exists():
            shutil.rmtree(config_target, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        if profile_stage and profile_stage.exists():
            shutil.rmtree(profile_stage, ignore_errors=True)
