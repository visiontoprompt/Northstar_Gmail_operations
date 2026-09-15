# Gmail Operations SaaS — Canonical Architecture

This document is the implementation structure behind [ROADMAP.md](../ROADMAP.md). It is derived from the approved architecture reference and is the source to use when deciding where a new feature belongs.

## System topology

```text
Admin / Employee Web Panel
        React + Vite
             |
             v
          FastAPI
     /       |        \
    v        v         v
PostgreSQL Redis     Secret Provider / KMS
    |        |
    |        v
    |   Scheduler / Dispatcher
    |        |
    |   Redis queue + distributed locks
    |        |
    +--------+-------------------+
             v                   v
       Stateless worker     Audit / metrics
             |
             v
      Playwright + real Chrome
        |                 |
        v                 v
 Persistent profile   Stable dedicated proxy
        |
        v
       Gmail UI
```

The API and database own business state. Redis owns temporary queue/lease state. Workers execute a job and can be replaced. A browser process never becomes the source of truth.

## Domain ownership

```text
Organization
  ├── AccountGroup (about 300 capacity)
  │     └── GmailAccount
  │            ├── AccountSecret / CredentialRevision
  │            ├── BrowserProfile (one per account)
  │            ├── ProxyAssignment (one stable assignment)
  │            ├── AccountModuleState[]
  │            ├── BrowserSession[]
  │            ├── JobItem[]
  │            └── AuditEvent[]
  ├── Employee / Team / RoleAssignment
  ├── ProvisioningJob[]
  ├── WorkerNode[]
  ├── ContactSet[]
  ├── ExtensionCatalog[]
  ├── OperationTemplate[]
  └── Issue[]
```

The group owns membership and policy scope. It does not own browser processes or profiles. Accounts can move between groups and employees without changing their identity, secret reference, profile identity, or proxy identity.

## Account state model

Do not collapse these into one status field.

```text
Onboarding: NEW → SETUP_REQUIRED → READY_FOR_LOGIN
                       └──────────→ INVALID / DUPLICATE
Any state:              → ARCHIVED

Authentication: NOT_STARTED → LOGIN_REQUIRED → AUTHENTICATED
                                      └───────→ VERIFICATION_REQUIRED

Operation: QUEUED → CLAIMED → RUNNING → VERIFYING → COMPLETED
                                      ├───────────→ ATTENTION
                                      └───────────→ FAILED / RETRYABLE

Session: LOGIN_REQUIRED → RUNNING → CLOSED
                    ├──────────→ NEEDS_ATTENTION
                       (VERIFICATION_REQUIRED / UNKNOWN_PAGE)
```

`STARTING` is reserved in the active-session set for a future startup window. `CRASHED`
and `LAUNCH_FAILED` are not persisted as session rows: a browser launch failure raises
`BrowserLaunchError` before any session is written, so the account/profile stay clean
and the next attempt retries from scratch instead of colliding with a zombie session.
The session `login_state` carries `REQUIRED`, `AUTHENTICATED`, `VERIFICATION_REQUIRED`,
or `UNKNOWN_PAGE`; the free-standing `state` distinguishes lifecycle (`LOGIN_REQUIRED`,
`RUNNING`, `NEEDS_ATTENTION`, `CLOSED`).

Subsystem states should include at least authentication, Gmail, proxy, profile, security, contacts, extensions, configuration, and worker/runtime. The account’s overall state is derived from those states and evidence, not manually guessed.

## Backend module boundaries

Current repository modules and their intended ownership:

```text
backend/app/
  config.py             settings and environment boundaries
  db.py                 SQLAlchemy engine/session/base
  models.py             durable domain entities
  schemas.py            API input/output validation
  security.py           encryption and secret separation
  service.py            transactional account/session/job operations
  login_policy.py       browser page classification and safe signals
  runtime.py            isolated Chrome/profile launch boundary
  observer.py           CDP observation and human-attention transitions
  worker/
    queue.py            Redis queue and progress state
    locks.py            account/profile lease ownership
    worker.py           dispatcher and worker lifecycle
    redis.py            Redis connection boundary
```

Browser launch semantics (`runtime.py`): each login opens a **dedicated fresh Chrome
window** with its own `--user-data-dir` and per-process CDP endpoint
(`--remote-debugging-port=0`, read from `DevToolsActivePort`). Any leftover Chrome
process still bound to the profile directory is terminated before launch
(`_terminate_stale_browser`) so the new session never piggybacks on a previous window.
A launch that cannot start (config off, missing executable, no CDP endpoint) raises
`BrowserLaunchError`; `service.py` persists nothing in that case, so retries start
clean.

Observer semantics (`observer.py` and `login_policy.py`): the observer attaches to the
account Chrome via CDP, classifies the current page, and is **consent-only**. It
auto-advances ordinary consent interstitials (`accounts.*`/`consent.*`) only when no
visible email/password field exists, and it records every page state through
`observe_session_page`. A real mailbox observation fires `LOGIN_COMPLETED`; a
CAPTCHA, MFA/security check, or unknown page flips the session to `NEEDS_ATTENTION`
(a human-gated pause) and stops that session's observer. It never fills or submits
credentials, never clears cookies, and never manipulates stealth signals.

Logging: `main.py` configures structured INFO logging at startup (`gmail_ops` root
logger), and every critical transition logs — job state changes, browser launch
result and failure reason, observer start/pause, item finalization, and session close. Use the API/audit events for operational truth and the logs for diagnosis walls.

Target additions as later phases are implemented:

```text
backend/app/
  auth/                 identity provider, roles, organization scope
  accounts/             registry and assignment domain
  profiles/             storage nodes, backups, profile health
  proxies/              stable assignments and health checks
  jobs/                 generic job state machine and checkpoints
  operations/            contacts, labels, apps, extensions, alerts, mail
  health/               subsystem checks and issue generation
  observability/        audit, metrics, traces, retention
  workers/               node registration, capacity, drain, heartbeats
```

The existing `service.py` can remain the Phase 1 composition boundary while the product is small; these modules are the extraction plan when the domain grows, not permission to duplicate business rules.

## Frontend structure

The frontend is an operational control room, not a generic CRM dashboard.

```text
frontend/src/
  App.tsx                current Phase 1 shell and route composition
  styles.css             canonical visual tokens and component styles
  session-contract.ts    safe session response/interaction contract

Target feature areas:
  routes/dashboard       issue-first overview and group context
  routes/groups          group capacity, policy, and operations
  routes/accounts        registry, detail, assignments, credentials
  routes/factory         provisioning queue, concurrency, checkpoints
  routes/sessions        live sessions and human-attention workspace
  routes/employees       access, assignment, and hand-off
  routes/infrastructure  proxies, profiles, workers
  routes/configuration   templates, contacts, extensions, alerts
  routes/observability   issues, activity, audit, reports
  components/            shared table, modal, form, status, toast patterns
```

Required information hierarchy:

1. Current group context and global search.
2. Critical or action-required accounts.
3. Group operations and capacity.
4. Account Factory / provisioning progress.
5. Active sessions.
6. Proxy, profile, and worker health.

The visual language remains the approved dark navigation rail, light work canvas, restrained surfaces, Space Grotesk headings, Inter controls, IBM Plex Mono operational identifiers, amber hand-off states, teal confirmed states, and red destructive/error states.

## Data and secret boundaries

Safe account metadata may include email, group, assignment, lifecycle states, masked recovery hint, 2FA configured boolean, proxy/profile references, timestamps, and health summaries.

Secret storage contains encrypted password, recovery material, 2FA seed, and future session-secret references. It is accessed only inside an authorized operation boundary. Browser profile directories are durable sensitive state and are never returned through the API.

Audit events contain actor, organization, action, target IDs, result, timestamp, and bounded operational metadata. They never contain raw import lines, passwords, recovery values, 2FA seeds, cookies, email bodies, or arbitrary page text.

## Request lifecycle

```text
UI action
  → authenticated API request
  → organization / role / group authorization
  → schema validation
  → transaction + audit event
  → enqueue job when external work is required
  → worker claims job and profile lock
  → launch a dedicated fresh Chrome window (or raise BrowserLaunchError; nothing persisted, item requeued)
  → persist the session only after a successful launch
  → observer attaches over the per-process CDP endpoint
  → concrete observation + consent-only advance; challenge → human attention
  → durable state update + audit event
  → UI refresh / live progress
```

A launch failure is recorded in logs and raises before any session row is written, so
the account never accumulates a stale open-session claim. External work must not be
reported as successful optimistically. A queued operation, an opened browser, an
authenticated Gmail state, and a verified configuration are separate milestones.

## Scaling rules

- Group capacity defaults to approximately 300 and is enforced transactionally.
- Per-job concurrency and global active-profile caps are explicit configuration.
- One profile cannot have two active writers.
- Stable proxy assignment is maintained during normal account operation; proxy rotation is not a default behavior.
- Worker capacity is measured from CPU, memory, browser count, and observed job latency.
- Profiles remain on durable storage nodes with tested backup/recovery; they are not copied between workers for every job.
- Queue state is recoverable from durable job items when Redis is unavailable.

## Runtime configuration

Environment-driven settings (`backend/.env`, see `.env.example`) that affect the
browser boundary:

- `BROWSER_LAUNCH_ENABLED` — master switch for launching a local Chrome process. When
  `false`, login sessions cannot open and dispatch raises `BrowserLaunchError`
  (items are requeued, nothing is persisted).
- `BROWSER_EXECUTABLE` — Chrome binary resolved on `PATH` (`google-chrome` default).
- `PROFILE_ROOT` — directory holding the persistent per-account user-data folders.
  Defaults to `<project-root>/data/browser-profiles` (at the SaaS project root).
- `BROWSER_OBSERVER_ENABLED` — defaults `true` when a browser is launched; `false`
  selects a pure human hand-off.
- `BROWSER_CDP_PORT` — deprecated compatibility setting; the runtime always asks
  Chrome for a random per-profile CDP port (`--remote-debugging-port=0`).
- `BROWSER_OBSERVE_INTERVAL` — seconds between observer classification polls.
- `ADMIN_API_TOKEN` — required (outside development) for the bearer-token middleware
  that guards every non-health `/api/v1` route.
- `REDIS_URL`, `WORKER_*` — queue, lock TTLs, heartbeat, stale detection, retry,
  active-profile cap, and auto-dispatch.

## Verification gates

Every phase must add the relevant tests before it is marked complete:

- model/migration and transaction tests;
- API authorization, validation, idempotency, and no-secrets tests;
- queue, lock, retry, stale-recovery, and concurrency tests;
- Playwright/Chrome smoke tests at the isolated runtime boundary;
- frontend keyboard, loading, error, destructive-confirmation, and responsive tests;
- load/benchmark tests before increasing active-profile limits;
- audit assertions for every state-changing action.

No phase is complete because a screen exists. Its backend contract, state transitions, failure behavior, audit trail, and verification gate must all exist.
