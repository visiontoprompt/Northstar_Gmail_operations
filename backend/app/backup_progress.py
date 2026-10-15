"""Short-lived, administrator-scoped progress for synchronous backup requests."""
from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_operations: dict[str, dict[str, Any]] = {}
_TTL_SECONDS = 3600


def begin(operation_id: str, owner_id: str, kind: str) -> None:
    with _lock:
        now = time.monotonic()
        for key, item in list(_operations.items()):
            if now - item["updated"] > _TTL_SECONDS:
                del _operations[key]
        _operations[operation_id] = {
            "owner_id": owner_id, "kind": kind, "stage": "Preparing", "percent": None,
            "state": "running", "updated": now,
        }


def update(operation_id: str | None, stage: str, completed: int | None = None, total: int | None = None) -> None:
    if not operation_id:
        return
    percent = round(100 * completed / total) if completed is not None and total and total > 0 else None
    with _lock:
        item = _operations.get(operation_id)
        if item and item["state"] == "running":
            item.update(stage=stage, percent=max(0, min(100, percent)) if percent is not None else None, updated=time.monotonic())


def finish(operation_id: str | None, success: bool) -> None:
    if not operation_id:
        return
    with _lock:
        item = _operations.get(operation_id)
        if item:
            item.update(state="complete" if success else "failed", stage="Complete" if success else "Failed", percent=100 if success else None, updated=time.monotonic())


def get(operation_id: str, owner_id: str) -> dict[str, Any] | None:
    with _lock:
        item = _operations.get(operation_id)
        if not item or item["owner_id"] != owner_id or time.monotonic() - item["updated"] > _TTL_SECONDS:
            return None
        return {key: item[key] for key in ("kind", "stage", "percent", "state")}
