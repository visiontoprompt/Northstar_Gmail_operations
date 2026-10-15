from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.backup as backup
from app.backup import BackupError, _decrypt_file, _encrypt_file, _from_json_value, _json_value
from app.models import Organization


def test_encrypted_backup_round_trip_keeps_credentials_private(tmp_path: Path):
    source = tmp_path / "payload.tar"
    encrypted = tmp_path / "backup.nstar"
    restored = tmp_path / "restored.tar"
    source.write_bytes(b"credentials=secret-token\n" * 1000)

    _encrypt_file(source, encrypted, "a-strong-backup-passphrase")

    assert b"secret-token" not in encrypted.read_bytes()
    _decrypt_file(encrypted, restored, "a-strong-backup-passphrase")
    assert restored.read_bytes() == source.read_bytes()


def test_encrypted_backup_rejects_wrong_password_and_tampering(tmp_path: Path):
    source = tmp_path / "payload.tar"
    encrypted = tmp_path / "backup.nstar"
    source.write_bytes(b"private backup contents")
    _encrypt_file(source, encrypted, "a-strong-backup-passphrase")

    with pytest.raises(BackupError, match="incorrect or the backup is damaged"):
        _decrypt_file(encrypted, tmp_path / "wrong.tar", "another-strong-passphrase")

    data = bytearray(encrypted.read_bytes())
    data[-20] ^= 0x01
    encrypted.write_bytes(data)
    with pytest.raises(BackupError, match="incorrect or the backup is damaged"):
        _decrypt_file(encrypted, tmp_path / "tampered.tar", "a-strong-backup-passphrase")


def test_backup_rejects_short_passphrase(tmp_path: Path):
    with pytest.raises(BackupError, match="at least 12 characters"):
        _encrypt_file(tmp_path / "missing", tmp_path / "backup.nstar", "too-short")


def test_database_json_tags_round_trip_datetime_and_binary():
    from datetime import datetime, timezone

    value = {"created_at": datetime(2026, 10, 15, tzinfo=timezone.utc), "blob": b"profile"}
    assert _from_json_value(_json_value(value)) == value


def test_restore_replaces_database_and_profile_data_transactionally(tmp_path: Path, monkeypatch):
    source_engine = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    target_engine = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    backup.Base.metadata.create_all(source_engine)
    backup.Base.metadata.create_all(target_engine)
    source_profile = tmp_path / "source-profiles"
    (source_profile / "profile-a").mkdir(parents=True)
    (source_profile / "profile-a" / "Preferences").write_text("saved-profile", encoding="utf-8")
    target_profile = tmp_path / "target-profiles"
    (target_profile / "old-profile").mkdir(parents=True)
    (target_profile / "old-profile" / "old.txt").write_text("old", encoding="utf-8")
    settings = SimpleNamespace(profile_root=str(source_profile), demo_org_id="org-source", model_dump=lambda: {"secret_key": "test-master-key"})
    monkeypatch.setattr(backup, "engine", source_engine)
    monkeypatch.setattr(backup, "get_settings", lambda: settings)
    with source_engine.begin() as connection:
        connection.execute(Organization.__table__.insert().values(id="org-source", name="Source", created_at=datetime(2026, 10, 15, tzinfo=timezone.utc)))
    archive = backup.create_backup("a-long-and-safe-passphrase")
    settings.profile_root = str(target_profile)
    with target_engine.begin() as connection:
            connection.execute(Organization.__table__.insert().values(id="org-target", name="Target", created_at=datetime(2026, 10, 15, tzinfo=timezone.utc)))
    try:
        with Session(target_engine, expire_on_commit=False) as session:
            restored = backup.restore_backup(archive, "a-long-and-safe-passphrase", session, actor_id="admin-test", safety_backup_name="safety-test.nstar")
            assert restored["table_count"] == len(backup.Base.metadata.tables)
        with Session(target_engine) as session:
            assert [item.id for item in session.scalars(select(Organization)).all()] == ["org-source"]
        assert (target_profile / "profile-a" / "Preferences").read_text(encoding="utf-8") == "saved-profile"
        assert not (target_profile / "old-profile").exists()
    finally:
        archive.parent and __import__("shutil").rmtree(archive.parent, ignore_errors=True)
