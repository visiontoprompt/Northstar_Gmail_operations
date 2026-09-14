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

Session: STARTING → RUNNING → CLOSING → CLOSED
             ├──────────────→ ATTENTION
             ├──────────────→ CRASHED
             └──────────────→ LAUNCH_FAILED
```

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
  → isolated Playwright/Chrome execution
  → concrete observation + checkpoint
  → durable state update + audit event
  → UI refresh / live progress
```

External work must not be reported as successful optimistically. A queued operation, an opened browser, an authenticated Gmail state, and a verified configuration are separate milestones.

## Scaling rules

- Group capacity defaults to approximately 300 and is enforced transactionally.
- Per-job concurrency and global active-profile caps are explicit configuration.
- One profile cannot have two active writers.
- Stable proxy assignment is maintained during normal account operation; proxy rotation is not a default behavior.
- Worker capacity is measured from CPU, memory, browser count, and observed job latency.
- Profiles remain on durable storage nodes with tested backup/recovery; they are not copied between workers for every job.
- Queue state is recoverable from durable job items when Redis is unavailable.

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
