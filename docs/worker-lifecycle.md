# Login worker lifecycle

This increment turns the login batch queue into a Redis-backed worker pipeline:
per-job concurrency, a global active-profile cap, single-lease locking per account,
stale-worker recovery, and automatic dispatch of the next queued account when a
session finishes.

## Boundaries

- Login state and the page classifier still come from `login_policy.py`. The worker
  observes pages and maps `CAPTCHA`/`MFA_OR_SECURITY_CHECK`/`UNKNOWN` to an
  `ATTENTION` pause for a human operator.
- The worker does **not** solve CAPTCHA/MFA, submit secrets, bypass security checks,
  or manipulate stealth/fingerprint signals. Session payloads and audit events carry
  account/profile/job IDs only; credentials stay in the encrypted secret store.

## Runtime topology

- **Dispatcher** (`app/worker/worker.py`) starts with the API process when
  `WORKER_ENABLED=true`. Each tick: refresh heartbeats, recover stale locks, dispatch
  ready items for `RUNNING` jobs.
- **Queue** (`app/worker/queue.py`) keeps a Redis zset per job
  (`gmailops:v1:queue:{job_id}`) scored by creation time, a shared active-profile set,
  and a per-job running set.
- **Locks** (`app/worker/locks.py`) acquire one Redis lock per account/profile
  (`SET NX PX`), refreshed with WATCH/MULTI (no Lua for fakeredis parity), and bound
  a real `session_id` once the browser session exists. The `job_locks` table mirrors
  each lease for durable recovery and audit.

## State model

`LoginJobItem`: `QUEUED -> RUNNING -> SUCCEEDED | ATTENTION | FAILED | CANCELLED`.
On a challenge/unknown page the item is finalized as `ATTENTION` and its profile stays
reserved for a human. Closing a session before authentication releases the lease,
marks the item back to `QUEUED`, and pauses the batch for operator review.

## Retry and stale recovery

- Dispatch increments `attempts`. `recover_stale_locks` finds `RUNNING` items whose
  `heartbeat_at` is older than `WORKER_STALE_SECONDS`: if `attempts >=
  WORKER_RETRY_LIMIT` the item is `FAILED`, otherwise it is re-queued. The mirror
  `job_locks` rows are released and Redis locks force-released in both cases.
- Leases without an item (lock row only) are released the same pass.

## API

- `GET /api/v1/login-jobs/{id}/progress` — per-item states, counters, and percent.
- `POST /api/v1/login-jobs/{id}/dispatch-next` — manual dispatch wave (API lease
  `orchestrator-api`).
- `POST /api/v1/login-jobs/{id}/pause|resume|cancel` — batch control; resume re-enqueues
  `QUEUED` items and dispatches.
- `POST /api/v1/maintenance/recover-stale-sessions` — runs one recovery pass and
  returns `{requeued, failed, released}`.

## Configuration

`REDIS_URL`, `WORKER_ENABLED`, `WORKER_POLL_INTERVAL`, `WORKER_HEARTBEAT_TTL`,
`WORKER_HEARTBEAT_INTERVAL`, `WORKER_STALE_SECONDS`, `WORKER_LOCK_TTL`,
`WORKER_RETRY_LIMIT`, `WORKER_MAX_ACTIVE_PROFILES`, `WORKER_RELEASE_ON_COMPLETE`.
Tests run against `fakeredis://` with a short poll interval (0.1 s).

## Browser observer

When `BROWSER_LAUNCH_ENABLED` and `BROWSER_OBSERVER_ENABLED` are both true, the
launched Chrome opens with `--remote-debugging-port` (a random free port when
`0`), and `BrowserObserver`
(`app/observer.py`) connects over CDP with Playwright.

The local one-command launcher deliberately sets both `BROWSER_OBSERVER_ENABLED=false`
and `BROWSER_AUTO_LOGIN=false`, so Google authentication happens manually in a
standard Chrome surface. CDP/observer automation is deferred to an explicitly
approved worker environment and is not part of the supported local mode.

- Each poll reads URL/title/visible text, classifies it with `classify_page`, and
  records it via `observe_session_page`.
- **Auto-advance is consent-only.** Only ordinary consent interstitials
  (`accounts.*`/`consent.*`) are clicked (`_click_consent`) when no visible
  email/password field is present. Nothing else is ever automated.
- A `GMAIL_HOME` observation fires `LOGIN_COMPLETED` → the item becomes `SUCCEEDED`
  and the queue advances to the next account automatically (that is the trigger
  that drives the batch forward).
- CAPTCHA, MFA/security-check, and unknown pages pause the batch for a human by
  flipping the session to `ATTENTION`; the observer stops for that session.
- The observer never fills or submits credentials/recovery codes, never clears
  cookies, and never manipulates stealth signals. It is a daemon thread per
  session, torn down on close/cancel/stale-recovery via `_stop_observer`.
- `playwright` (Python, `>=1.62`) is a runtime dependency; it connects over CDP, so
  no Playwright browser binaries are required — the worker's own Chrome is observed.

`BROWSER_OBSERVER_ENABLED`, `BROWSER_OBSERVE_INTERVAL`, and the deprecated
`BROWSER_CDP_PORT` compatibility setting configure the observer in `.env.example`.
The runtime always requests a random per-profile CDP port so concurrent sessions
cannot share one debugging endpoint.
