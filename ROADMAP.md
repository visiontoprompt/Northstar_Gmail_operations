# Gmail Operations SaaS — Master Roadmap

This is the canonical delivery roadmap for the company-owned Gmail operations platform. It consolidates the approved product direction, the architecture reference, the prototype information hierarchy, and the current repository status.

The platform manages authorized company Gmail accounts, isolated browser profiles, employee assignments, controlled operations, and operational history. It is not an email client and it is not an anti-detection tool.

## Non-negotiable architecture rules

1. One account = one persistent browser profile = one assigned stable proxy = one active profile lock = one PostgreSQL source-of-truth record.
2. A group of approximately 300 accounts is an organizational and scheduling boundary, not 300 Chrome processes.
3. Workers are disposable execution capacity. They do not own account state.
4. Every account operation is resumable, idempotent where possible, auditable, and based on concrete browser/application events.
5. Account health is split by subsystem. A single generic `Healthy` field is not sufficient.
6. Credentials, recovery data, 2FA secrets, cookies, and session tokens never appear in ordinary logs, audit metadata, browser storage, URLs, or API responses.
7. The system supports only accounts the organization owns or is authorized to operate.
8. Google authentication, CAPTCHA, MFA, and security checks are never bypassed or solved by stealth/fingerprint manipulation. A challenge becomes an explicit human-attention state.
9. Playwright + real Chrome + persistent user-data directories are the browser execution boundary. Gmail API/OAuth is not the browser-control layer.
10. Central Chrome policies are preferred for organization-wide browser security and extensions; the SaaS verifies compliance.

## Delivery status

- **Complete** — implemented and verified in this repository.
- **Partial** — a safe contract or scaffold exists, but production behavior is not complete.
- **Planned** — intentionally not implemented yet.
- **Human-gated** — the platform pauses and records the state; an authorized operator must complete the external security step.

## Phase 0 — Product and platform foundation

**Outcome:** A secure, testable service boundary with the approved visual and domain language.

Scope:

- React/Vite admin console and FastAPI service.
- PostgreSQL-compatible schema and migration discipline; SQLite remains local development only.
- Organization boundary and future role-based authorization boundary.
- Encrypted secret handling separated from account metadata.
- Structured audit events with a no-secrets rule.
- Design tokens, accessibility target (WCAG 2.2 AA), semantic status vocabulary, and issue-first operational UX direction.

Exit criteria:

- Every API path enforces organization ownership and production authentication.
- Secrets are absent from responses, logs, audit events, client storage, and error messages.
- The data model can represent independent account, profile, proxy, worker, job, session, health, and audit state.

**Status: Partial.** The local service, migrations, encryption boundary, audit model, design tokens, and production bearer guard exist. A real identity provider/RBAC layer is still required before deployment.

## Phase 1 — Account registry and onboarding

**Outcome:** An administrator can safely create the account inventory and hand records to the next phase as `READY_FOR_LOGIN` without claiming that Google authentication succeeded.

Scope:

- Organization account groups with approximately 300-account capacity.
- Single add and bulk import using `email:password:recovery:2fa`.
- In-memory parsing, row validation, normalization, duplicate detection, preview, idempotent import, and capacity validation.
- Encrypted account secrets separated from safe account metadata.
- Dedicated proxy assignment metadata and encrypted per-account proxy connection; no proxy secret in account responses.
- One reserved persistent-profile record per account.
- Explicit onboarding states: `NEW`, `SETUP_REQUIRED`, `READY_FOR_LOGIN`, `DUPLICATE`, `INVALID`, `ARCHIVED`.
- Search, group filter, status filter, server-backed pagination, admin account table, account detail shell, edit metadata, move group, guarded archive, and audit history.
- Versioned credential-revision records that invalidate readiness when credentials change.

Exit criteria:

- A valid import is encrypted before persistence and never echoed as a raw line after completion.
- Duplicate and full-group cases are deterministic and audited.
- Moving or archiving an account cannot cross organization boundaries or leave an active profile lock behind.
- The registry clearly distinguishes stored credentials from authenticated Gmail state.

**Status: Complete for the local Phase 1 scope.** Real employee assignment, real proxy provisioning, and real profile storage belong to later phases.

## Phase 2 — Isolated login session hand-off

**Outcome:** An authorized operator can open a unique isolated Chrome session for an account, preserve its profile storage, and record the result without touching the SaaS browser session.

Scope:

- One durable browser profile per account.
- One session lock per active account/profile.
- A dedicated, fresh Chrome window per opened account, isolated from the SaaS console and from any earlier instance for that profile (leftover Chrome processes bound to the profile dir are terminated before launch; launch uses `--new-window --no-session-restore` with a dedicated user-data directory).
- Session lifecycle: `LOGIN_REQUIRED → RUNNING → CLOSED`, with `NEEDS_ATTENTION` for challenge/unknown pages and `STARTING` reserved in the active-state set. Launch failures raise `BrowserLaunchError` **before** a session row is written, so a failed attempt never leaves a zombie session or a locked profile blocking retry.
- Single-session open, close, resume, and safe session history.
- Observation-first page classification (Playwright attached over a per-profile CDP endpoint) for login form, Gmail mailbox, ordinary interstitial, CAPTCHA, MFA/security check, and unknown pages.
- Consent-only auto-advance: the observer clicks only ordinary consent interstitials (`accounts.*`/`consent.*`) when no visible email/password field is present.
- Optional automated credential entry (`BROWSER_AUTO_LOGIN`, default on): the observer types the account's stored encrypted email and password into Google's own login host (`accounts.google.com`/`consent.google.com`) after a `LOGIN_FORM` classification, skipping already-filled fields and attempting each page once. Recovery/2FA codes and CAPTCHA/security answers are never entered; those pages remain human-gated.
- A valid mailbox observation marks the session `AUTHENTICATED`; a challenge or unknown page flips the session to a human-gated attention state and keeps the profile reservation. The observer keeps watching after a challenge so the mailbox is still detected once a human clears it.
- Separate random CDP endpoint per browser process; `BROWSER_CDP_PORT` is kept only as a deprecated compatibility setting.

Exit criteria:

- The SaaS console and account Chrome session are separate browser environments. *(Holds locally.)*
- A profile cannot be opened concurrently by two workers or employees.
- A Gmail mailbox is marked authenticated only from a valid observed mailbox state or an explicit approved event.
- Closing or crashing a session releases locks safely and preserves the profile reference and audit history. *(Close, cancel, and stale-recovery paths release locks and stop the observer; launch failures persist nothing.)*

**Status: Complete for the local single-node Phase 2 scope.** Dedicated fresh-window Chrome launch, persistent profile metadata, batch hand-off, locks, session lifecycle, launch-failure retry, visible end-to-end logging, the safe observer boundary, and account-scoped proxy launch are implemented and tested. Auto-driven sign-in (stored email/password entered into Google's own login host) is implemented behind `BROWSER_AUTO_LOGIN`; Google challenges (CAPTCHA, MFA, security checks) remain human-gated. Multi-node profile storage and employee assignment belong to later phases.

The local admin console now also exposes dedicated **Sessions**, **Browser profiles**, and **Proxies** inventory pages. Sessions show one latest record per account with group scope; profiles show only physical profile directories that exist; proxies support safe bulk testing, provider/location observations, assignment changes, editing, and removal. These pages preserve the account → profile → session boundary and never expose credential or proxy-password material.

## Phase 3 — Provisioning pipeline and resumable jobs

**Outcome:** Account preparation is a durable pipeline that can run at controlled concurrency and resume from the last completed step.

Scope:

- Job types: `LOGIN`, `HEALTH_CHECK`, `REPAIR`, `CONFIGURATION`, `CONTACT_SYNC`, `EXTENSION_SYNC`, and future approved operations.
- Job and job-item records with account/group IDs, priority, attempt count, worker lease, timestamps, state, and bounded error reason.
- Per-account module states instead of one overall status.
- State machine: `QUEUED → CLAIMED → PROFILE_LOCKED → RUNNING → VERIFYING → COMPLETED | ATTENTION | FAILED`.
- Redis queue, distributed locks, heartbeats, stale-worker recovery, retry policy, and global active-profile cap.
- Group-level batch controls with explicit concurrency, pause, resume, cancel, progress, and audit trail.
- Idempotent step handlers and checkpoints so a failed job resumes at the failed module.

Exit criteria:

- A 300-account group can be queued without launching 300 browsers.
- A worker failure returns work to the queue safely.
- A profile lock, job lease, session, and database state cannot disagree after recovery.
- Operators can see queued, running, attention, failed, and completed work in the Account Factory.

**Status: Partial.** Login queue, Redis-compatible locks, concurrency controls, recovery, and worker scaffolding exist. Dispatch automatically requeues items whose browser launch failed so a later wave can retry them cleanly. General provisioning modules and production worker deployment remain planned.

## Phase 4 — Account preparation operations

**Outcome:** Approved, reviewable account operations can be run through the isolated profile pipeline and verified per module.

Each operation must have a typed configuration, version, approval/audit record, job item, retry policy, and verification result. A saved plan is not an executed action.

Planned modules:

- Credential change: password, recovery method, and 2FA rotation with encrypted revisioning and re-authentication state.
- Browser security: organization-approved Enhanced Safe Browsing and related managed Chrome policy verification.
- Gmail markers and labels: create, update, map, and verify required labels.
- Google apps: create/configure approved Google application resources and verify selected options.
- Contacts: upload reusable CSV/VCF/TXT assets, validate and deduplicate email addresses, select a file from the registry, synchronize it to one account or a selected/group scope through the isolated profile, and report per-account results. Initial file-library and profile-backed import are implemented; versioned contact sets and full per-account verification remain planned.
- Add-ons/extensions: approved catalog keyed by Chrome Web Store ID, permissions review, required version, installation policy, and installed/missing/wrong-version states.
- Profile personalization: controlled profile name, theme, and approved per-account settings.
- Google Alerts: template-based topic configuration with reviewable settings and per-account verification.
- Mail operations: approved send/reply workflows with explicit authorization, templates, recipient validation, rate limits, and immutable send audit records.

Exit criteria:

- No operation can read secrets outside its authorized worker boundary.
- Every operation is restartable and reports module-level verification.
- Bulk execution honors group and global concurrency limits.
- External security challenges stop the affected item for human attention.

**Status: Partial.** The registry `Labels & Markers` action now executes the approved Gmail settings operation for authenticated saved profiles, with localized verification, audit events, and automatic session close. The `Browser security` action now executes the same profile-backed lifecycle for Enhanced Safe Browsing at Google Account security, using localized semantic control detection, verification, audit events, and automatic session close. The remaining Phase 4 modules continue to use preparation-only drafts until their workers are implemented.

## Phase 5 — Employees, access, and assignment

**Outcome:** Employees can access only the accounts assigned to them, while admins can safely move accounts between people and groups.

Scope:

- Employees, teams/departments, managers, primary assignment, backup assignment, and assignment history.
- Roles: Super Admin, Administrator/Team Manager, Employee/Agent, Auditor/Read-only.
- Organization and group-scoped authorization on every read and write.
- Employee account view with permitted open/resume/close/report actions.
- Admin reassignment, bulk reassignment, hand-off confirmation, conflict detection, and audit trail.
- Session ownership, active-user visibility, termination permissions, and retention policy.

Exit criteria:

- An employee cannot discover or open an unassigned account.
- Reassignment preserves account/profile identity and never copies or resets a profile implicitly.
- Audit history identifies actor, previous owner, new owner, group, result, and time.

**Status: Planned.** The current demo actor is not production RBAC.

## Phase 6 — Health, issues, and observability

**Outcome:** The first opening of the app tells the administrator what needs attention and why.

Scope:

- Issue-first dashboard and Attention Center.
- Independent health for authentication, Gmail, proxy, profile, security, contacts, extensions, configuration, and worker/runtime.
- Health checks that produce concrete evidence, not guessed labels.
- Issue lifecycle: open, acknowledged, assigned, human action, resolved, reopened.
- Live sessions with employee, account, group, duration, worker/node, and action.
- Activity feed, audit log, incident details, reports, and bounded operational metrics.
- Proxy health: reachability, latency, last check, stable assignment.
- Profile health: readable, locked, storage node, size, browser version, backup, last open/close.

Exit criteria:

- Admin lands on critical/action-required accounts before broad metrics.
- Every issue links to the failed subsystem, last evidence, safe recovery action, and audit history.
- No email content or unnecessary employee browsing content is captured for monitoring.

**Status: Partial.** Audit events and attention states exist at the session boundary. The full health center, issue inbox, and operational dashboard are planned.

## Phase 7 — Worker fleet and scale-out

**Outcome:** The platform can operate 1,000+ accounts by adding worker capacity rather than coupling accounts to a fixed browser server.

Scope:

- Stateless browser workers with node identity, capacity, CPU/RAM/Chrome telemetry, heartbeat, and drain mode.
- Scheduler placement using profile affinity and resource capacity.
- Durable profile storage nodes, backups, retention, and recovery without unnecessary profile copying.
- PostgreSQL for durable state, Redis for queues/locks/heartbeats, and a managed secret store/KMS for secrets.
- API replicas behind a load balancer; worker nodes scale independently.
- Benchmark-driven active profile limits based on real Gmail pages, tabs, extensions, CPU, and memory.
- Structured logs, metrics, traces, alerts, and dead-letter handling.

Exit criteria:

- Workers can be replaced without losing account state.
- A node drain safely finishes or requeues leases.
- Profile storage has tested backup and recovery procedures.
- Capacity limits are measured and enforced per job, group, node, and global fleet.

**Status: Partial.** Redis-compatible queue/lock concepts and active-profile limits are scaffolded. Multi-node deployment and storage operations are planned.

## Phase 8 — Production hardening and release

**Outcome:** A secure, observable, supportable product ready for controlled company use.

Scope:

- Real identity provider, MFA, RBAC, organization tenancy, and secret manager/KMS.
- PostgreSQL deployment, backups, migration runner, encryption/key rotation, and retention policies.
- Rate limits, CSRF/CORS policy, request validation, authorization tests, and security review.
- Browser/runtime compatibility matrix and Playwright/Chrome smoke tests.
- Load tests for groups of 300 and fleets of 1,000+ accounts with safe concurrency.
- Disaster recovery, incident runbooks, operator documentation, and release checklist.
- GitHub CI for tests, build, migrations, dependency checks, and deployment artifacts.

**Status: Planned.** Local development is intentionally not production deployment.

## Canonical product navigation target

The final navigation follows the approved prototype direction:

```text
OVERVIEW
  Dashboard
  Attention Center

OPERATIONS
  Account Factory
  Groups
  Accounts
  Sessions

PEOPLE
  Employees

INFRASTRUCTURE
  Proxies
  Browser Profiles
  Workers

CONFIGURATION
  Templates
  Contacts
  Browser Apps / Extensions

OBSERVABILITY
  Activity
  Audit Logs
  Reports

SYSTEM
  Settings
```

The current UI intentionally exposes the Phase 1 Account Registry first. Future navigation items must become enabled only when their phase has a real backend contract and verification coverage.

## Definition of done for the whole product

The product is not complete merely because accounts can be imported or a Chrome window can open. The whole roadmap is complete only when:

- account, group, employee, proxy, profile, job, worker, health, session, operation, and audit entities have durable ownership and authorization;
- each account can be prepared through resumable, concurrency-controlled, verifiable modules;
- employees can safely use assigned accounts and admins can reassign them;
- the dashboard leads with actionable problems;
- the system handles Google verification/security decisions through explicit human attention states;
- profile locks, worker leases, retries, backups, and recovery are tested at scale; and
- secrets and production authorization have passed a security review.
