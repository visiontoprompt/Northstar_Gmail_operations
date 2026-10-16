from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.backup as backup
import app.backup_progress as backup_progress
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


def test_backup_progress_is_scoped_to_its_administrator():
    backup_progress.begin("test-progress-id", "admin-one", "download")
    backup_progress.update("test-progress-id", "Exporting database tables", 2, 4)
    assert backup_progress.get("test-progress-id", "admin-one")["percent"] == 50
    assert backup_progress.get("test-progress-id", "admin-two") is None
    backup_progress.finish("test-progress-id", True)
    assert backup_progress.get("test-progress-id", "admin-one")["state"] == "complete"


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
    measured: list[tuple[str, int | None, int | None]] = []
    archive = backup.create_backup("a-long-and-safe-passphrase", persistent=True, kind="workspace", created_by="admin-test", progress=lambda stage, done, total: measured.append((stage, done, total)))
    listed = backup.list_backup_archives()
    assert len(listed) == 1
    assert listed[0]["name"] == archive.name
    assert listed[0]["kind"] == "workspace"
    assert listed[0]["table_count"] == len(backup.Base.metadata.tables)
    assert listed[0]["profile_files"] == 1
    assert listed[0]["created_by"] == "admin-test"
    assert listed[0]["size_bytes"] == archive.stat().st_size
    assert backup.backup_archive_path(archive.name) == archive
    assert backup.backup_archive_path("../northstar.env") is None
    assert any(stage == "Exporting database tables" and done == total for stage, done, total in measured)
    assert any(stage == "Encrypting archive" and done == total for stage, done, total in measured)
    settings.profile_root = str(target_profile)
    with target_engine.begin() as connection:
            connection.execute(Organization.__table__.insert().values(id="org-target", name="Target", created_at=datetime(2026, 10, 15, tzinfo=timezone.utc)))
    try:
        with Session(target_engine, expire_on_commit=False) as session:
            restored = backup.restore_backup(archive, "a-long-and-safe-passphrase", session, actor_id="admin-test", safety_backup_name="safety-test.nstar", progress=lambda stage, done, total: measured.append((stage, done, total)))
            assert restored["table_count"] == len(backup.Base.metadata.tables)
            assert any(stage == "Restoring database tables" and done == total for stage, done, total in measured)
        with Session(target_engine) as session:
            assert [item.id for item in session.scalars(select(Organization)).all()] == ["org-source"]
        assert (target_profile / "profile-a" / "Preferences").read_text(encoding="utf-8") == "saved-profile"
        assert not (target_profile / "old-profile").exists()
    finally:
        archive.parent and __import__("shutil").rmtree(archive.parent, ignore_errors=True)
