"""Redis-backed priority queue for LoginJobItem dispatch.

Members are encoded as ``json.dumps({"item_id": ..., "account_id": ...})`` and
scored by the item's created-at timestamp (or a re-scheduling delay). The
queue is scoped per job so concurrency limits are enforced per job while a
dedicated structure tracks the global active-profile count cap.
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis

from .redis import KEY_PREFIX, get_redis


def _qkey(job_id: str) -> str:
    return f"{KEY_PREFIX}:queue:{job_id}"


def _active_key() -> str:
    return f"{KEY_PREFIX}:active_profiles"


def _running_key(job_id: str) -> str:
    return f"{KEY_PREFIX}:running:{job_id}"


def _member(item_id: str, account_id: str) -> str:
    return json.dumps({"item_id": item_id, "account_id": account_id}, sort_keys=True, separators=(",", ":"))


def encode_member(item_id: str, account_id: str) -> str:
    return _member(item_id, account_id)


def decode_member(member: str) -> dict[str, str]:
    return json.loads(member)


class RedisQueue:
    """Enqueue/dequeue/requeue primitives for login job items."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis()

    # -- job-scoped queue ------------------------------------------------
    def enqueue(self, job_id: str, item_id: str, account_id: str, when: float) -> None:
        self.client.zadd(_qkey(job_id), {_member(item_id, account_id): when})

    def enqueue_many(self, job_id: str, entries: list[tuple[str, str, float]]) -> None:
        if not entries:
            return
        mapping: dict[str, float] = {_member(item_id, account_id): when for item_id, account_id, when in entries}
        self.client.zadd(_qkey(job_id), mapping)

    def pop_next(self, job_id: str) -> dict[str, str] | None:
        popped = self.client.zpopmin(_qkey(job_id), count=1)
        if not popped:
            return None
        member, _score = popped[0]
        return decode_member(member)

    def requeue(self, job_id: str, item_id: str, account_id: str, when: float | None = None) -> None:
        self.client.zadd(_qkey(job_id), {_member(item_id, account_id): when if when is not None else time.time()})

    def queued_count(self, job_id: str) -> int:
        return int(self.client.zcard(_qkey(job_id)) or 0)

    def clear(self, job_id: str) -> None:
        self.client.delete(_qkey(job_id))

    def peek(self, job_id: str, limit: int = 20) -> list[dict[str, str]]:
        rows = self.client.zrange(_qkey(job_id), 0, limit - 1)
        return [decode_member(row) for row in rows]

    # -- global active-profile cap --------------------------------------
    def add_active_profile(self, account_id: str) -> int:
        self.client.sadd(_active_key(), account_id)
        return int(self.client.scard(_active_key()) or 0)

    def remove_active_profile(self, account_id: str) -> int:
        self.client.srem(_active_key(), account_id)
        return int(self.client.scard(_active_key()) or 0)

    def active_profile_count(self) -> int:
        return int(self.client.scard(_active_key()) or 0)

    # -- per-job running set (mirrors database running_count) ------------
    def add_running(self, job_id: str, account_id: str) -> None:
        self.client.sadd(_running_key(job_id), account_id)

    def remove_running(self, job_id: str, account_id: str) -> None:
        self.client.srem(_running_key(job_id), account_id)

    def running_count(self, job_id: str) -> int:
        return int(self.client.scard(_running_key(job_id)) or 0)

    def clear_running(self, job_id: str) -> None:
        self.client.delete(_running_key(job_id))