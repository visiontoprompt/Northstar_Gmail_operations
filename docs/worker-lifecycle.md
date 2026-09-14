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