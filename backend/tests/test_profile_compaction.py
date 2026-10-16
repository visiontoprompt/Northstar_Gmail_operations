from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import AccountGroup, BrowserProfile, BrowserSession, GmailAccount, Organization
from app import profile_compaction, service


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'accounts.db'}")
    Base.metadata.create_all(engine)
    root = tmp_path / "browser-profiles"
    monkeypatch.setattr(profile_compaction, "get_settings", lambda: SimpleNamespace(profile_root=str(root)))
    monkeypatch.setattr(service, "local_browser_profiles_alive", lambda ids: set())
    with Session(engine) as db:
        db.add(Organization(id="org-compact", name="Compaction test"))
        db.add(AccountGroup(id="group-compact", organization_id="org-compact", name="Compaction group"))
        db.add(GmailAccount(id="acct-compact", organization_id="org-compact", group_id="group-compact",
            email="account@example.com", normalized_email="account@example.com", profile_id="account@example.com__org_compact",
            profile_status="SAVED", login_status="AUTHENTICATED"))
        db.add(BrowserProfile(id="account@example.com__org_compact", organization_id="org-compact", account_id="acct-compact",
            storage_reference="profiles/org-compact/acct-compact", status="SAVED", lock_state="UNLOCKED",
            authenticated_at=datetime.now(timezone.utc)))
        db.commit()
        yield db, root / "account@example.com__org_compact"
    engine.dispose()


def put(path: Path, content: bytes = b"important") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_compaction_removes_only_exact_cache_directories(workspace):
    db, root = workspace
    put(root / "Local State")
    put(root / "Default" / "Network" / "Cookies")
    put(root / "Default" / "Preferences")
    put(root / "Default" / "Local Storage" / "leveldb" / "data")
    put(root / "Default" / "IndexedDB" / "data")
    put(root / "Default" / "Service Worker" / "CacheStorage" / "data")
    put(root / "Default" / "Cache" / "Cache_Data" / "entry", b"cached" * 100)
    put(root / "Default" / "Code Cache" / "js" / "entry", b"compiled")
    put(root / "Default" / "GPUCache" / "entry", b"gpu")
    put(root / "Default" / "not-a-Cache" / "important")
    result = service.compact_account_profile(db, "org-compact", "admin", "acct-compact")
    assert result["status"] == "COMPACTED"
    assert result["removed_bytes"] == len(b"cached" * 100) + len(b"compiled") + len(b"gpu")
    assert not (root / "Default" / "Cache").exists()
    assert not (root / "Default" / "Code Cache").exists()
    assert not (root / "Default" / "GPUCache").exists()
    for kept in ("Local State", "Default/Network/Cookies", "Default/Preferences", "Default/Local Storage/leveldb/data",
                 "Default/IndexedDB/data", "Default/Service Worker/CacheStorage/data", "Default/not-a-Cache/important"):
        assert (root / kept).read_bytes() == b"important"


def test_live_or_locked_profile_is_not_compacted(workspace):
    db, root = workspace
    put(root / "Default" / "Cache" / "entry", b"cached")
    profile = db.get(BrowserProfile, "account@example.com__org_compact")
    profile.lock_state = "LOCKED"
    db.commit()
    result = service.compact_account_profile(db, "org-compact", "admin", "acct-compact")
    assert result["status"] == "SKIPPED_ACTIVE"
    assert (root / "Default" / "Cache" / "entry").exists()
    profile.lock_state = "UNLOCKED"
    db.add(BrowserSession(id="session-open", organization_id="org-compact", account_id="acct-compact",
        profile_id=profile.id, state="RUNNING", login_state="AUTHENTICATED", runtime_type="LOCAL_CHROME"))
    db.commit()
    assert service.compact_account_profile(db, "org-compact", "admin", "acct-compact")["status"] == "SKIPPED_ACTIVE"
    assert (root / "Default" / "Cache" / "entry").exists()


def test_symlinked_cache_is_not_followed(workspace, tmp_path):
    db, root = workspace
    outside = tmp_path / "outside" / "keep"
    put(outside)
    (root / "Default").mkdir(parents=True)
    (root / "Default" / "Cache").symlink_to(outside.parent, target_is_directory=True)
    result = service.compact_account_profile(db, "org-compact", "admin", "acct-compact")
    assert result["status"] == "UNCHANGED"
    assert outside.read_bytes() == b"important"


def test_explicit_close_compacts_after_session_record_is_closed(workspace, monkeypatch):
    db, root = workspace
    put(root / "Default" / "Cache" / "entry", b"cached")
    profile = db.get(BrowserProfile, "account@example.com__org_compact")
    profile.lock_state = "LOCKED"
    profile.active_session_id = "session-open"
    db.add(BrowserSession(id="session-open", organization_id="org-compact", account_id="acct-compact",
        profile_id=profile.id, state="RUNNING", login_state="AUTHENTICATED", runtime_type="LOCAL_CHROME"))
    db.commit()
    monkeypatch.setattr(service, "_stop_observer", lambda _session_id: True)
    monkeypatch.setattr(service, "_maybe_dispatch_next", lambda _db, _job: None)
    service.close_session(db, "org-compact", "admin", "session-open")
    assert not (root / "Default" / "Cache").exists()
    assert db.get(BrowserSession, "session-open").state == "CLOSED"


def test_observer_post_close_callback_compacts_terminal_session(workspace, monkeypatch):
    db, root = workspace
    put(root / "Default" / "Cache" / "entry", b"cached")
    profile = db.get(BrowserProfile, "account@example.com__org_compact")
    db.add(BrowserSession(id="session-finished", organization_id="org-compact", account_id="acct-compact",
        profile_id=profile.id, state="CLOSED", login_state="AUTHENTICATED", runtime_type="LOCAL_CHROME",
        closed_at=datetime.now(timezone.utc), metadata_json={}))
    db.commit()
    monkeypatch.setattr(service, "SessionLocal", lambda: Session(db.get_bind()))
    service._on_observer_browser_closed("session-finished")
    assert not (root / "Default" / "Cache").exists()
    assert (root / "Default").exists()
