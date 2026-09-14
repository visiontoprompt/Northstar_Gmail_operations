"""Redis-backed login worker infrastructure.

Owns queue placement, per-account locking, heartbeats, and the distributed
state machinery for LoginJob/LoginJobItem. It never handles Gmail credential
entry, password submission, recovery codes, 2FA, CAPTCHA/MFA bypass, or
stealth/fingerprint manipulation.

The background loop lives in ``app.worker.worker`` and is imported lazily by
the application entrypoint to avoid a service/worker import cycle.
"""

from .locks import RedisLocks
from .queue import RedisQueue

__all__ = ["RedisLocks", "RedisQueue"]