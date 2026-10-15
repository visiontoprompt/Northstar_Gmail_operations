"""Shared Redis client access.

Production uses ``redis://`` URLs against a real Redis. Test environments can
set ``REDIS_URL=fakeredis://`` to use an in-process store so the worker
machinery can be exercised without a live server.
"""

from __future__ import annotations

import redis

from ..config import get_settings

_server: object | None = None
_client: redis.Redis | None = None

# Key namespace shared across the worker infrastructure.
KEY_PREFIX = "gmailops:v1"


def _build_client() -> redis.Redis:
    global _server
    settings = get_settings()
    if settings.redis_url.startswith("fakeredis"):
        # Fakeredis is a development/test extra, so keep it out of the
        # production import path when the app uses the real Redis service.
        import fakeredis

        _server = fakeredis.FakeServer()
        return fakeredis.FakeRedis(server=_server, decode_responses=True)
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def reset_redis() -> None:
    """Drop cached client so tests can rebuild against a clean store."""
    global _client
    _client = None


def flush_redis() -> None:
    if _client is not None:
        _client.flushdb()
