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


def _newsletter_bulk_running_key(scope: str) -> str:
    return f"{KEY_PREFIX}:newsletter_bulk_running:{scope}"


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

    def running_members(self, job_id: str) -> set[str]:
        return {str(member) for member in self.client.smembers(_running_key(job_id))}

    def bulk_running_members(self, scope: str) -> set[str]:
        return {str(member) for member in self.client.smembers(_newsletter_bulk_running_key(scope))}

    def ensure_members(self, job_id: str, entries: list[tuple[str, str, float]]) -> int:
        """Insert members that are not queued yet, without touching existing scores.

        A mirror rebuild runs on every worker tick. Re-adding the score of a
        member that is already queued would overwrite a scheduled retry delay
        and defeat the ``pop_next`` guard, so rehydration must be additive.
        """
        if not entries:
            return 0
        mapping = {_member(item_id, account_id): when for item_id, account_id, when in entries}
        return int(self.client.zadd(_qkey(job_id), mapping, nx=True) or 0)

    def pop_next(self, job_id: str) -> dict[str, str] | None:
        popped = self.client.zpopmin(_qkey(job_id), count=1)
        if not popped:
            return None
        member, score = popped[0]
        # A retry is scheduled with a future score. Do not immediately pop it
        # again in the same dispatch wave, otherwise one transient launch
        # error burns every retry attempt in a tight loop.
        if score > time.time():
            self.client.zadd(_qkey(job_id), {member: score})
            return None
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

    def active_profile_members(self) -> set[str]:
        return {str(member) for member in self.client.smembers(_active_key())}

    # -- per-job running set (mirrors database running_count) ------------
    def add_running(self, job_id: str, account_id: str) -> None:
        self.client.sadd(_running_key(job_id), account_id)

    def remove_running(self, job_id: str, account_id: str) -> None:
        self.client.srem(_running_key(job_id), account_id)

    def running_count(self, job_id: str) -> int:
        return int(self.client.scard(_running_key(job_id)) or 0)

    def clear_running(self, job_id: str) -> None:
        self.client.delete(_running_key(job_id))

    # -- shared newsletter bulk-run concurrency ------------------------
    # A UI bulk action may create several durable jobs (one per account
    # group). This scope makes their configured concurrency one global cap.
    def add_newsletter_bulk_running(self, scope: str, task_id: str) -> None:
        self.client.sadd(_newsletter_bulk_running_key(scope), task_id)

    def remove_newsletter_bulk_running(self, scope: str, task_id: str) -> None:
        self.client.srem(_newsletter_bulk_running_key(scope), task_id)

    def newsletter_bulk_running_count(self, scope: str) -> int:
        return int(self.client.scard(_newsletter_bulk_running_key(scope)) or 0)

    def clear_newsletter_bulk_running(self, scope: str) -> None:
        self.client.delete(_newsletter_bulk_running_key(scope))
