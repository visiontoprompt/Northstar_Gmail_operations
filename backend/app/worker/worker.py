"""Background login worker loop.

The worker owns clock-driven duties only:

- refresh heartbeats / Redis lock TTLs for its own leases;
- detect and recover stale worker leases;
- dispatch the next concurrency-limited wave of queued accounts.

It is intentionally forbidden from touching credentials: it creates sessions,
moves queue state, and pauses accounts that raised CAPTCHA / MFA / security /
unknown pages. Everything else is operator territory.
"""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import LoginJob, NewsletterJob

logger = logging.getLogger(__name__)


class LoginWorker:
    def __init__(self) -> None:
        from ..service import _worker_token

        self.settings = get_settings()
        self.worker_id = _worker_token()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="login-worker", daemon=True)
        self._thread.start()
        logger.info("login worker started as %s", self.worker_id)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def wake(self) -> None:
        """Interrupt the idle poll delay after an operation releases capacity."""
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - keep the loop alive
                logger.exception("login worker tick failed")
            elapsed = time.monotonic() - started
            self._wake.wait(max(0.0, self.settings.worker_poll_interval - elapsed))
            self._wake.clear()

    def tick(self) -> None:
        from ..newsletter_service import dispatch_newsletter_job, recover_stale_newsletter_tasks, refresh_newsletter_task_heartbeats
        from ..service import dispatch_ready_items, recover_stale_locks, reconcile_queue_mirrors, refresh_worker_heartbeats

        db = SessionLocal()
        try:
            org_id = self.settings.demo_org_id
            refresh_worker_heartbeats(db, org_id, self.worker_id)
            refresh_newsletter_task_heartbeats(db, org_id, self.worker_id)
            recover_stale_locks(db, org_id)
            recover_stale_newsletter_tasks(db, org_id)
            # The mirrors enforce the concurrency limits, so a leaked member
            # silently freezes every later wave. Reconcile before dispatching.
            try:
                reconcile_queue_mirrors(db, org_id)
            except Exception:  # noqa: BLE001 - never block dispatch on a mirror fix
                logger.exception("failed to reconcile dispatch mirrors")
                db.rollback()
            for job_id in db.scalars(select(LoginJob.id).where(LoginJob.organization_id == org_id, LoginJob.status == "RUNNING")).all():
                try:
                    dispatch_ready_items(db, org_id, self.worker_id, job_id)
                except Exception:  # noqa: BLE001 - one job must not kill the loop
                    logger.exception("failed to dispatch job %s", job_id)
                    db.rollback()
            for job_id in db.scalars(select(NewsletterJob.id).where(NewsletterJob.organization_id == org_id, NewsletterJob.status == "RUNNING")).all():
                try:
                    dispatch_newsletter_job(db, org_id, self.worker_id, job_id)
                except Exception:  # noqa: BLE001 - one newsletter job must not kill the loop
                    logger.exception("failed to dispatch newsletter job %s", job_id)
                    db.rollback()
        finally:
            db.close()


_worker: LoginWorker | None = None


def get_login_worker() -> LoginWorker:
    global _worker
    if _worker is None:
        _worker = LoginWorker()
    return _worker


def start_login_worker() -> LoginWorker:
    worker = get_login_worker()
    worker.start()
    return worker


def stop_login_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
