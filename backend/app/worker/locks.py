"""Redis lock (fast mutual exclusion) + heartbeat for account/profile leases.

A lock is acquired with ``SET NX PX`` so only one worker can lease an account
and its persistent browser profile at a time. The lock token is verified on
refresh/release (via a WATCH/MULTI optimistic transaction) so a worker never
clobbers a lease that has already been recovered. The durable ``job_locks``
row (see models.JobLock) records the same lease in the database for audit and
crash recovery.
"""

from __future__ import annotations

import json
import uuid

import redis

from .redis import KEY_PREFIX, get_redis


def account_lock_key(account_id: str) -> str:
    return f"{KEY_PREFIX}:lock:{account_id}"


def _encode(token: str, worker_id: str, session_id: str) -> str:
    return json.dumps({"token": token, "worker_id": worker_id, "session_id": session_id}, sort_keys=True, separators=(",", ":"))


def _decode(value: str) -> dict[str, str]:
    return json.loads(value)


class RedisLocks:
    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis()

    def acquire(self, account_id: str, worker_id: str, session_id: str, ttl_ms: int) -> str | None:
        """Return the lock token when the lease is acquired, else None."""
        token = uuid.uuid4().hex
        ok = self.client.set(account_lock_key(account_id), _encode(token, worker_id, session_id), nx=True, px=ttl_ms)
        return token if ok else None

    def bind_session(self, account_id: str, token: str, session_id: str) -> bool:
        """Attach the durable browser-session id to an already-held lease."""
        key = account_lock_key(account_id)
        current = self.client.get(key)
        if not current:
            return False
        payload = _decode(current)
        if payload["token"] != token:
            return False
        ttl = int(self.client.pttl(key) or 0)
        if ttl <= 0:
            return False
        self.client.set(key, _encode(token, payload["worker_id"], session_id), xx=True, px=ttl)
        return True

    def refresh(self, account_id: str, token: str, ttl_ms: int) -> bool:
        key = account_lock_key(account_id)
        pipe = self.client.pipeline(transaction=True)
        while True:
            try:
                pipe.watch(key)
                current = pipe.get(key)
                if not current or _decode(current)["token"] != token:
                    pipe.reset()
                    return False
                pipe.multi()
                pipe.pexpire(key, ttl_ms)
                pipe.execute()
                return True
            except redis.WatchError:
                continue
            finally:
                pipe.reset()

    def release(self, account_id: str, token: str) -> bool:
        key = account_lock_key(account_id)
        pipe = self.client.pipeline(transaction=True)
        while True:
            try:
                pipe.watch(key)
                current = pipe.get(key)
                if not current or _decode(current)["token"] != token:
                    pipe.reset()
                    return False
                pipe.multi()
                pipe.delete(key)
                pipe.execute()
                return True
            except redis.WatchError:
                continue
            finally:
                pipe.reset()

    def held(self, account_id: str) -> dict[str, str] | None:
        value = self.client.get(account_lock_key(account_id))
        if not value:
            return None
        return _decode(value)

    def force_release(self, account_id: str) -> None:
        self.client.delete(account_lock_key(account_id))
