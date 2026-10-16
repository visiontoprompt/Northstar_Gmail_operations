"""Bounded, account-mapped import of portable Chrome profile archives."""
from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator

import rarfile
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .config import get_settings
from .models import AuditEvent, BrowserProfile, BrowserSession, GmailAccount
from .runtime import local_browser_profiles_alive

MAX_ARCHIVE_BYTES = 20 * 1024**3
MAX_EXTRACTED_BYTES = 40 * 1024**3
MAX_FILES = 100_000
ACTIVE_SESSION_STATES = {"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}
TRANSIENT_CHROME_FILES = {"SingletonCookie", "SingletonLock", "SingletonSocket", "DevToolsActivePort"}


class ProfileImportError(ValueError):
    pass


def _profile_id(account: GmailAccount) -> str:
    value = account.browser_profile.id if account.browser_profile else account.profile_id
    if not value or not re.fullmatch(r"[A-Za-z0-9@_.+-]+", value) or value in {".", ".."}:
        raise ProfileImportError(f"Account {account.email} has no safe profile ID.")
    return value


def _archive_path(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not normalized or "\x00" in normalized or any(part in {".", ".."} for part in path.parts):
        raise ProfileImportError("Archive contains an unsafe path.")
    if any(":" in part for part in path.parts):
        raise ProfileImportError("Archive contains an unsafe path.")
    return path.parts


@contextmanager
def _open_archive(path: Path) -> Iterator[zipfile.ZipFile | rarfile.RarFile]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".zip" and zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                yield archive
            return
        if suffix == ".rar" and rarfile.is_rarfile(path):
            with rarfile.RarFile(path) as archive:
                yield archive
            return
    except (zipfile.BadZipFile, rarfile.Error, OSError) as exc:
        raise ProfileImportError("The ZIP or RAR archive could not be read.") from exc
    raise ProfileImportError("Choose a valid .zip or .rar archive.")


def _members(archive: zipfile.ZipFile | rarfile.RarFile):
    members = []
    for info in archive.infolist():
        parts = _archive_path(info.filename)
        if isinstance(info, zipfile.ZipInfo):
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ProfileImportError("Archive links and special files are not supported.")
            is_dir = info.is_dir()
        else:
            if info.is_symlink():
                raise ProfileImportError("Archive links are not supported.")
            is_dir = info.is_dir()
            if not is_dir and not info.is_file():
                raise ProfileImportError("Archive contains an unsupported file type.")
        if not is_dir:
            members.append((info, parts))
    if not members:
        raise ProfileImportError("The archive contains no profile files.")
    if len(members) > MAX_FILES or sum(info.file_size for info, _ in members) > MAX_EXTRACTED_BYTES:
        raise ProfileImportError("The archive exceeds the profile import file or extracted-size limit.")
    return members


def _alias_map(accounts: list[GmailAccount]) -> dict[str, GmailAccount]:
    aliases = {}
    for account in accounts:
        for name in (account.id, account.email, _profile_id(account)):
            key = name.casefold()
            if key in aliases and aliases[key].id != account.id:
                raise ProfileImportError("Account profile folder names are ambiguous.")
            aliases[key] = account
    return aliases


def _mapped_members(members, accounts: list[GmailAccount], single_account: GmailAccount | None):
    aliases = _alias_map(accounts)
    mapped: dict[str, list[tuple[object, tuple[str, ...]]]] = {}
    unmatched: set[str] = set()
    all_paths = [parts for _, parts in members]
    common_wrapper = len({parts[0].casefold() for parts in all_paths}) == 1 and all(len(parts) > 1 for parts in all_paths)
    for info, parts in members:
        if parts[0] in {"__MACOSX", ".DS_Store"} or parts[-1] in {".DS_Store", "Thumbs.db"}:
            continue
        found = next(((index, aliases[part.casefold()]) for index, part in enumerate(parts[:-1]) if part.casefold() in aliases), None)
        if single_account:
            account = single_account
            if found and found[1].id == account.id:
                relative = parts[found[0] + 1:]
            elif common_wrapper and parts[0] not in {"Default", "Profile 1", "System Profile", "Network"}:
                relative = parts[1:]
            else:
                relative = parts
        elif found:
            account = found[1]
            relative = parts[found[0] + 1:]
        else:
            unmatched.add(parts[1] if common_wrapper and len(parts) > 2 else parts[0])
            continue
        if not relative or relative[-1] in TRANSIENT_CHROME_FILES:
            continue
        mapped.setdefault(account.id, []).append((info, relative))
    if not mapped:
        raise ProfileImportError("No archive folder matched an existing account email, account ID, or profile ID.")
    return mapped, sorted(unmatched)[:50]


def _extract(archive, mapped, stage: Path) -> None:
    written = 0
    for account_id, entries in mapped.items():
        seen: set[str] = set()
        for info, relative in entries:
            key = "/".join(relative).casefold()
            if key in seen:
                raise ProfileImportError("Archive contains duplicate profile file paths.")
            seen.add(key)
            target = stage / account_id / Path(*relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as destination:
                while block := source.read(1024 * 1024):
                    written += len(block)
                    if written > MAX_EXTRACTED_BYTES:
                        raise ProfileImportError("Extracted profiles exceed the size limit.")
                    destination.write(block)
            os.chmod(target, 0o600)
        account_stage = stage / account_id
        if not (account_stage / "Local State").is_file() and not any((account_stage / name).is_dir() for name in ("Default", "Profile 1", "System Profile")):
            raise ProfileImportError("A matched folder is not a Chrome user-data profile (expected Local State or Default/Profile 1).")


def import_profiles(db: Session, organization_id: str, actor_id: str, archive_path: Path, policy: str, account_id: str | None = None) -> dict:
    if policy not in {"skip", "replace"}:
        raise ProfileImportError("Choose skip or replace for existing profiles.")
    accounts = list(db.scalars(select(GmailAccount).options(joinedload(GmailAccount.browser_profile)).where(
        GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED",
        *([GmailAccount.id == account_id] if account_id else []),
    )).unique().all())
    if account_id and not accounts:
        raise ProfileImportError("Account not found.")
    root = Path(get_settings().profile_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".profile-import-", dir=root.parent))
    try:
        with _open_archive(archive_path) as archive:
            members = _members(archive)
            mapped, unmatched = _mapped_members(members, accounts, accounts[0] if account_id else None)
            _extract(archive, mapped, stage)
        account_map = {account.id: account for account in accounts}
        results = []
        for mapped_id in mapped:
            account = account_map[mapped_id]
            profile_id = _profile_id(account)
            target = root / profile_id
            profile = account.browser_profile
            active = db.scalar(select(BrowserSession.id).where(BrowserSession.account_id == account.id,
                BrowserSession.state.in_(ACTIVE_SESSION_STATES), BrowserSession.closed_at.is_(None)).limit(1))
            if active or (profile and (profile.lock_state == "LOCKED" or profile.active_session_id)) or profile_id in local_browser_profiles_alive({profile_id}):
                results.append({"account_id": account.id, "email": account.email, "status": "LOCKED"})
                continue
            if target.exists() and policy == "skip":
                results.append({"account_id": account.id, "email": account.email, "status": "SKIPPED"})
                continue
            safety = None
            if target.exists():
                safety = root.parent / "profile-import-safety" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") / f"{profile_id}-{uuid.uuid4().hex[:8]}"
                safety.parent.mkdir(parents=True, exist_ok=True)
            incoming = stage / account.id
            installed = False
            try:
                if safety:
                    target.rename(safety)
                incoming.rename(target)
                installed = True
                if not profile:
                    profile = BrowserProfile(id=profile_id, organization_id=organization_id, account_id=account.id,
                        storage_reference=account.profile_storage_ref or f"profiles/{organization_id}/{account.id}", status="SAVED", lock_state="UNLOCKED")
                    db.add(profile)
                # Preserve the imported user-data directory without claiming
                # its Google session is still valid on this server. The first
                # browser launch checks Gmail and records authentication only
                # after observing the signed-in page.
                profile.status = "IMPORTED"
                profile.authenticated_at = None
                account.profile_id = profile_id
                account.profile_status = "IMPORTED"
                account.login_status = "PROFILE_IMPORTED"
                account.gmail_status = "NOT_CHECKED"
                db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=organization_id, actor_id=actor_id,
                    action="browser_profile.imported", target_type="browser_profile", target_id=profile_id, result="success",
                    metadata_json={"account_id": account.id, "replaced": bool(safety), "format": archive_path.suffix.lower().lstrip(".")}))
                db.commit()
                results.append({"account_id": account.id, "email": account.email, "status": "REPLACED" if safety else "IMPORTED", "safety_copy": str(safety) if safety else None})
            except Exception:
                db.rollback()
                if installed and target.exists():
                    shutil.rmtree(target)
                if safety and safety.exists() and not target.exists():
                    safety.rename(target)
                results.append({"account_id": account.id, "email": account.email, "status": "FAILED"})
        return {"results": results, "unmatched": unmatched,
                "imported": sum(row["status"] == "IMPORTED" for row in results),
                "replaced": sum(row["status"] == "REPLACED" for row in results),
                "skipped": sum(row["status"] == "SKIPPED" for row in results),
                "locked": sum(row["status"] == "LOCKED" for row in results),
                "failed": sum(row["status"] == "FAILED" for row in results)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
