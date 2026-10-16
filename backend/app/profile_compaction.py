"""Remove only regenerable Chromium disk caches from a closed user-data dir.

Never walk the profile looking for generic 'cache' names: site storage and
Service Worker CacheStorage are persistent account data. This allowlist is
intentionally narrower than Chrome's own Clear Browsing Data operation.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path

from .config import get_settings


CACHE_DIRECTORIES = ("Cache", "Code Cache", "GPUCache", "Media Cache")
PROFILE_DIRECTORY = re.compile(r"Profile [1-9][0-9]*\Z")
SAFE_PROFILE_ID = re.compile(r"[A-Za-z0-9@_.+-]+\Z")


def _tree_bytes(path: Path) -> int:
    total = 0
    for base, directories, files in os.walk(path, followlinks=False):
        directories[:] = [name for name in directories if not (Path(base) / name).is_symlink()]
        for name in files:
            try:
                info = (Path(base) / name).lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def compact_closed_profile(profile_id: str) -> dict[str, int | str]:
    """Compact an idle profile; caller must hold its account start lock.

    This function only handles filesystem safety. The service checks DB locks,
    active sessions and live Chrome processes immediately before calling it.
    """
    if not SAFE_PROFILE_ID.fullmatch(profile_id) or profile_id in {".", ".."}:
        raise ValueError("Unsafe browser profile identifier")
    root = Path(get_settings().profile_root).resolve()
    target = root / profile_id
    if target.is_symlink() or not target.is_dir() or target.resolve() != target or not target.resolve().is_relative_to(root):
        return {"status": "MISSING", "removed_bytes": 0, "removed_directories": 0}
    bases = [target]
    for child in target.iterdir():
        if child.name in {"Default", "System Profile"} or PROFILE_DIRECTORY.fullmatch(child.name):
            if child.is_dir() and not child.is_symlink():
                bases.append(child)
    removed_bytes = 0
    removed_directories = 0
    for base in bases:
        for name in CACHE_DIRECTORIES:
            cache = base / name
            if cache.is_symlink() or not cache.is_dir():
                continue
            amount = _tree_bytes(cache)
            shutil.rmtree(cache)
            removed_bytes += amount
            removed_directories += 1
    return {"status": "COMPACTED" if removed_directories else "UNCHANGED", "removed_bytes": removed_bytes, "removed_directories": removed_directories}
