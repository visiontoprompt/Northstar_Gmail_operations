"""Bounded export and size inspection for persistent Chrome user-data profiles."""
from __future__ import annotations

import os
import re
import stat
import zipfile
from pathlib import Path
from typing import Iterable

MAX_PROFILE_FILES = 100_000
MAX_PROFILE_BYTES = 20 * 1024**3
TRANSIENT_FILES = {"SingletonCookie", "SingletonLock", "SingletonSocket", "DevToolsActivePort"}


def profile_path(root: Path, profile_id: str | None) -> Path | None:
    if not profile_id or not re.fullmatch(r"[A-Za-z0-9@_.+-]+", profile_id) or profile_id in {".", ".."}:
        return None
    base = root.resolve()
    candidate = (base / profile_id).resolve()
    if candidate.parent != base:
        return None
    return candidate


def _walk_profile(path: Path) -> Iterable[tuple[Path, os.stat_result]]:
    count = 0
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
        for name in files:
            if name in TRANSIENT_FILES:
                continue
            item = current_path / name
            try:
                info = item.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            count += 1
            if count > MAX_PROFILE_FILES:
                raise ValueError("Profile contains too many files to export safely.")
            yield item, info


def profile_size_bytes(path: Path | None) -> int | None:
    if path is None or not path.is_dir():
        return None
    return sum(info.st_size for _, info in _walk_profile(path))


def write_profiles_zip(root: Path, profiles: list[tuple[str, str]], destination: Path) -> tuple[int, int]:
    """Write (archive folder name, profile ID) pairs to a ZIP without following links."""
    total_bytes = 0
    total_files = 0
    resolved_root = root.resolve()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for folder_name, profile_id in profiles:
            path = profile_path(resolved_root, profile_id)
            if path is None or path.parent != resolved_root or not path.is_dir():
                raise FileNotFoundError(f"No profile directory is available for {folder_name}.")
            entries = list(_walk_profile(path))
            if not entries:
                raise FileNotFoundError(f"The profile directory for {folder_name} contains no regular files.")
            for item, info in entries:
                total_files += 1
                total_bytes += info.st_size
                if total_files > MAX_PROFILE_FILES or total_bytes > MAX_PROFILE_BYTES:
                    raise ValueError("Selected profiles exceed the 20 GB or 100,000 file export limit.")
                relative = item.relative_to(path).as_posix()
                archive_name = f"{folder_name}/{relative}"
                archive.write(item, archive_name, compress_type=zipfile.ZIP_STORED)
    return total_files, total_bytes
