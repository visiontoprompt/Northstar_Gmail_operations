from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import AccountGroup, BrowserProfile, GmailAccount, Organization
from app import profile_import
from app.profile_import import ProfileImportError, import_profiles
from app.service import profile_folder_name


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'accounts.db'}")
    Base.metadata.create_all(engine)
    root = tmp_path / "profiles"
    monkeypatch.setattr(profile_import, "get_settings", lambda: SimpleNamespace(profile_root=str(root)))
    monkeypatch.setattr(profile_import, "local_browser_profiles_alive", lambda ids: set())
    with Session(engine) as db:
        db.add(Organization(id="org-import", name="Import test"))
        db.add(AccountGroup(id="group-import", organization_id="org-import", name="Import group"))
        for account_id, email in (("acct-a", "a@example.com"), ("acct-b", "b@example.com")):
            db.add(GmailAccount(id=account_id, organization_id="org-import", group_id="group-import", email=email,
                normalized_email=email, profile_id=f"profile-{account_id}", profile_status="RESERVED"))
            db.add(BrowserProfile(id=f"profile-{account_id}", organization_id="org-import", account_id=account_id,
                storage_reference=f"profiles/org-import/{account_id}", status="RESERVED", lock_state="UNLOCKED"))
        db.commit()
        yield db, root, tmp_path
    engine.dispose()


def archive(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zip_file:
        for name, content in files.items():
            zip_file.writestr(name, content)
    return path


def test_bulk_import_maps_account_folders_and_skips_existing(workspace):
    db, root, tmp_path = workspace
    current = root / "profile-acct-a" / "Default"
    current.mkdir(parents=True)
    (current / "Preferences").write_bytes(b"original")
    zipped = archive(tmp_path / "profiles.zip", {
        "profiles/a@example.com/Default/Preferences": b"incoming-a",
        "profiles/b@example.com/Default/Preferences": b"incoming-b",
        "profiles/unknown@example.com/Default/Preferences": b"unknown",
    })
    result = import_profiles(db, "org-import", "admin", zipped, "skip")
    assert (result["imported"], result["skipped"], result["unmatched"]) == (1, 1, ["unknown@example.com"])
    assert (current / "Preferences").read_bytes() == b"original"
    assert (root / "profile-acct-b" / "Default" / "Preferences").read_bytes() == b"incoming-b"
    assert db.get(GmailAccount, "acct-b").profile_status == "SAVED"


def test_replace_keeps_previous_profile_and_refuses_locked_profile(workspace):
    db, root, tmp_path = workspace
    current = root / "profile-acct-a" / "Default"
    current.mkdir(parents=True)
    (current / "Preferences").write_bytes(b"original")
    zipped = archive(tmp_path / "one.zip", {"Default/Preferences": b"replacement"})
    profile = db.get(BrowserProfile, "profile-acct-a")
    profile.lock_state = "LOCKED"
    db.commit()
    blocked = import_profiles(db, "org-import", "admin", zipped, "replace", "acct-a")
    assert blocked["locked"] == 1
    assert (current / "Preferences").read_bytes() == b"original"
    profile.lock_state = "UNLOCKED"
    db.commit()
    result = import_profiles(db, "org-import", "admin", zipped, "replace", "acct-a")
    assert result["replaced"] == 1
    assert (current / "Preferences").read_bytes() == b"replacement"
    assert Path(result["results"][0]["safety_copy"]).joinpath("Default", "Preferences").read_bytes() == b"original"


def test_unsafe_archive_path_cannot_write_outside_profile_root(workspace):
    db, root, tmp_path = workspace
    zipped = archive(tmp_path / "traversal.zip", {"../a@example.com/Default/Preferences": b"bad"})
    with pytest.raises(ProfileImportError, match="unsafe path"):
        import_profiles(db, "org-import", "admin", zipped, "replace")
    assert not root.exists() or not any(root.iterdir())


def test_generated_account_profile_id_with_email_is_accepted(workspace):
    db, _root, _tmp_path = workspace
    account = db.get(GmailAccount, "acct-a")
    account.profile_id = profile_folder_name("org-import", account.email)
    account.browser_profile.id = account.profile_id
    db.commit()
    assert profile_import._profile_id(account) == "a@example.com__org_import"


def test_failed_install_restores_previous_profile(workspace, monkeypatch):
    db, root, tmp_path = workspace
    current = root / "profile-acct-a" / "Default"
    current.mkdir(parents=True)
    (current / "Preferences").write_bytes(b"original")
    zipped = archive(tmp_path / "one.zip", {"Default/Preferences": b"replacement"})
    rename = Path.rename

    def fail_stage_move(path: Path, target: Path):
        if path.name == "acct-a" and path.parent.name.startswith(".profile-import-"):
            raise OSError("test disk failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_stage_move)
    result = import_profiles(db, "org-import", "admin", zipped, "replace", "acct-a")
    assert result["failed"] == 1
    assert (current / "Preferences").read_bytes() == b"original"
