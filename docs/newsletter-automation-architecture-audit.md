# Newsletter Automation — Architecture Audit and Integration Plan

This document is the result of **PHASE 0** of the Newsletter Automation Goal: a
read-only audit of the existing Gmail Operations SaaS, followed by an adapted
integration design. It does not change application behavior.

**Hard rule honored throughout:** the module MUST NOT use the Gmail API. No new
Gmail API auth, scopes, credentials, or secondary Gmail integration. Mailbox
access uses the SaaS's existing browser-profile-based Gmail control.

---

## 1. Existing architecture

The SaaS is an admin control room for a company-owned Gmail account fleet
("Gmail Operations"). It is not an email client and explicitly not an
anti-detection tool.

- **Backend:** FastAPI + SQLAlchemy 2 + Pydantic v2 (`backend/app/`). The whole
  API surface lives in `backend/app/main.py` (~840 lines) with domain logic in
  `backend/app/service.py` (~3100 lines). Purpose-specific helpers live in
  small modules: `auth.py`, `contacts.py`, `alerts.py`, `addons.py`,
  `proxy.py`, `notifications.py`, `security.py`, `login_policy.py`,
  `observer.py`, `runtime.py`, `worker/`.
- **Frontend:** a single React + Vite app: `frontend/src/App.tsx` (~1690 lines)
  + `frontend/src/styles.css` (~770 lines). No router; navigation is
  `activeView` state. Auth via HttpOnly cookie; one `api()` fetch helper
  (App.tsx:73).
- **Database:** PostgreSQL in production, SQLite for local dev. String IDs with
  semantic prefixes (`audit_`, `session_`, `emp*`), `organization_id` foreign
  key on every tenant-scoped table, `created_at`/`updated_at=utcnow`,
  JSON columns for bounded metadata. Migrations are sequential SQL files
  (`backend/migrations/00NN_*.sql`); runtime `db.py` also applies targeted
  `_ensure_*` column guards for older databases.
- **Tenancy/permissions:** `Organization → AccountGroup → GmailAccount`
  (models.py:13, 162, 178). Employees (`Employee`, `EmployeeGroupAccess`) with
  roles `ADMINISTRATOR` / `ACCOUNT_OPERATOR` / `AUDITOR` and group access
  levels `VIEWER` / `OPERATOR` / `MANAGER` (auth.py:24, 41). Group access is
  enforced at the API boundary (`authorize_group`, auth.py:128).
- **Jobs:** durable job + item rows (`LoginJob`/`LoginJobItem`, models.py:376,
  398) with a Redis queue and in-process worker (see §5).
- **Audit:** every state-changing action writes an `AuditEvent`
  (models.py:289) with actor, target, result, and bounded `metadata_json`.
  **No-secrets rule:** credentials, cookies, recovery values, 2FA seeds, and
  email bodies never appear in audit metadata, logs, or API responses.
- **Notifications:** `notifications.py` provides Telegram + email (Resend/SMTP)
  transport, used e.g. to alert operators of operation failures
  (service.py:1842).

---

## 2. Existing Gmail architecture

Gmail accounts are controlled **exclusively through browser automation**:

1. **One account = one persistent Chrome profile = one lock = one session.**
   `BrowserProfile` (models.py:238) reserves a durable per-account user-data
   directory. `BrowserSession` (models.py:257) records each lifecycle
   (`LOGIN_REQUIRED → RUNNING → CLOSED`, with `NEEDS_ATTENTION`).
2. **Auth state lives in the profile**, not in tokens held by the app. After a
   profile is saved, `browser_profiles.authenticated_at` is set and subsequent
   "reuse" operations open Gmail already signed in (service.py:2587, 2618).
3. **No OAuth/Gmail API anywhere.** `pyproject.toml` has no google-auth/gmail
   client; the README and `docs/login-safety.md` are explicit that Gmail
   control is not via API/OAuth.
4. **The observer** (`observer.py`, class `BrowserObserver` at :217) connects
   over CDP (Playwright) to the account's Chrome, classifies pages via
   `login_policy.classify_page` (login_policy.py:35), and drives only approved
   surfaces. CAPTCHA, MFA/security checks, and unknown pages flip the session
   to `NEEDS_ATTENTION` / `VERIFICATION_REQUIRED` — **never bypassed**
   (login_policy.py:39-42; worker-lifecycle.md).
5. Credentials are decrypted only to be typed into Google's own sign-in host
   (`_resolve_login_credentials`, service.py:1450; hardened in observer.py's
   `_fill_sign_in_form`), and only when `BROWSER_AUTO_LOGIN=true`.

**Consequence for newsletter automation:** subscribing an account means opening
*that account's profile* in the isolated browser, navigating to the newsletter
signup page, and filling the form — the same boundary used for contacts,
labels, security, add-ons, and mail workflows. This satisfies account isolation
(§19 of the Goal) by construction (a profile belongs to exactly one account and
is profile-lock-exclusive).

---

## 3. Existing mailbox architecture

There is **no IMAP** and **no Gmail API**. Mailbox access is done by driving
the **Gmail web UI inside the account's own browser profile** through the
observer. This already exists as the "mail workflow" purpose:

- `purpose="mail"` starts an authenticated profile at
  `mail.google.com/mail/u/0/#category/promotions` or `#spam`
  (service.py:1365-1368), or the inbox for `reply`.
- The observer's `_handle_mail_tick` (observer.py:1826) then:
  - finds the localized search box (`_mail_search_input`, :1540);
  - runs a Gmail query built from a subject line + folder scope, e.g.
    `category:promotions subject:"..."` (`_mail_subject_query`, :1556);
  - selects visible subject-matching rows (`_mail_select_subject_rows`, :1587);
  - applies a bounded toolbar action (`_mail_click_action`, :1700) and
    verifies the result (`_mail_matching_row_count`, :1654;
    `_mail_action_confirmation_visible`, :1805);
  - emits `mail.*` events that finalize the session via
    `_report_observer_event` (service.py:1736, terminal set at :1742).
- **Current guardrail:** the mail workflow "never touches message bodies"
  (observer.py:1829-1832) and never opens arbitrary links.

**Consequence for confirmation email handling:** the abstraction the Goal calls
`MailboxAdapter` is, in this codebase, *[browser profile + observer Gmail
search]*. Confirmation detection reuses `_mail_search_input` /
`_mail_select_subject_rows` machinery but must **extend** it — opening the
matched message and extracting a validated confirmation URL from the body is a
new (bounded, allowlisted-domain) capability. Extraction must keep the
no-secrets rule: only the confirmation URL (validated against the newsletter
domain) may be captured; raw bodies are never stored or logged.

---

## 4. Existing browser architecture

- **Playwright (Python ≥1.62) + real Chrome/Chromium** with **persistent
  contexts** (`_launch_profile_context`, observer.py:631) — no bundled
  browsers, no `--remote-debugging-port`/`--enable-automation` flags on the
  Chrome command line the account sees (runtime.py & observer docstrings).
- Per-profile launch lifecycle in `runtime.py` (`launch_local_browser`,
  `_terminate_stale_browser`, `_find_chrome_pids_for_profile`). A failed launch
  raises `BrowserLaunchError` **before** any session row is written, so retries
  start clean (service.py:2612-2617; architecture.md:115-121).
- The observer is a **daemon thread per session**, polling the active page,
  classifying, and advancing state machines per `purpose`:
  `login`/`reuse`/`contacts`/`labels_markers`/`security`/`addons`/`mail`/
  `alerts`/`credential_change` (service.py:2585).
- Consent-only auto-advance; localized controls via attribute/aria/title/text
  scans (e.g. `_click_element_with_text`, `_click_submit`); load-settle window
  before judging a page (`BROWSER_OBSERVE_SETTLE_SECONDS`).
- **No stealth/fingerprint manipulation.** `docs/login-safety.md` is explicit.

**Consequence:** the newsletter signup browser worker must be a **new
`purpose="newsletter"` observer state machine** reusing `BrowserObserver`
launch/teardown, page settle logic, localized control clicking, and the
`ATTENTION` pause for CAPTCHA/human verification — exactly like the existing
`_handle_mail_tick` / `_handle_alerts_tick` / `_handle_addons_tick` handlers.
No second browser framework is introduced.

---

## 5. Existing queue architecture

- **Redis zset per job**: `gmailops:v1:queue:{job_id}`, entries
  `{item_id, account_id}` scored by create/retry time (`worker/queue.py`).
- **Redis locks per account/profile**: `SET NX PX`, token-verified refresh/
  release via WATCH/MULTI (no Lua for fakeredis parity) (`worker/locks.py`).
  Mirrored into durable `job_locks` rows for crash recovery and audit
  (models.py:455).
- **In-process dispatcher thread**: `LoginWorker.tick` (worker.py:61) refreshes
  heartbeats, recovers stale locks, and dispatches the next concurrency-limited
  wave for each `RUNNING` job. Enabled by `WORKER_ENABLED=true`.
- Per-job `concurrency_limit` + a global active-profile cap
  (`WORKER_MAX_ACTIVE_PROFILES`).
- Job controls: `start` / `dispatch-next` / `pause` / `resume` / `cancel`
  (service.py:1227, 2025, 3000; main.py:512-590), progress endpoint
  (service.py:2971 — per-item states + counters + percent).
- Stale detection: `recover_stale_locks` / `recover_orphaned_sessions`
  (service.py:1887, 3030) requeue or fail items past their lease.
- Tests use `fakeredis://` (conftest.py:7).

**Consequence:** bulk newsletter subscription is implemented as a
**`NewsletterJob` + `NewsletterTask`** pair that mirrors `LoginJob`/
`LoginJobItem` and reuses `RedisQueue`/`RedisLocks`/`LoginWorker` dispatch —
no new queue technology. Account-level isolation falls out of the existing
account lock (one lock per account, so a login job and a newsletter job can
never drive the same profile simultaneously).

---

## 6. Existing database architecture

See §1. Conventions that the new schema must follow (models.py throughout):

- String PKs with prefixes; `id = f"newsletter_{...}"` style (see
  `_new_id`, service.py:315).
- `organization_id` indexed FK on every table; tenant scope enforced in every
  query.
- `String(32..48)` state columns + `index=True` (never generic "Healthy" fields).
- `JSON` columns for bounded machine metadata (`metadata_json`,
  `configuration_json`).
- `UniqueConstraint` for idempotency (e.g. `uq_login_job_account`,
  models.py:400; `uq_import_org_key`, :305).
- Versioning pattern already exists: `CredentialRevision` (account, version,
  unique) and `OperationDraft` (account, operation_type, version).
- Migration discipline: new `NNNN_*.sql` files; runtime `create_all` +
  `_ensure_*` guards in db.py for older SQLite DBs.

---

## 7. Existing frontend architecture

- **Everything is in `App.tsx` + `styles.css`.** No router — `activeView`
  state, `WorkspaceView` union type (App.tsx:65, 176), inline view
  conditionals (:382-407).
- **Nav rail** (App.tsx:357-374): grouped `nav-label` + `nav-item` +
  `nav-badge`; sections "Workspace / Operations / Infrastructure / Settings".
- **Shared patterns** (all function-components inside App.tsx):
  `StatusBadge` (:877), `SummaryCard` (:834), `AttentionStrip` (:835),
  `Field`/`SecretField` (:900-901), `ToastRegion` (:1685), modal markup
  (`.modal-backdrop > .modal`), `.inline-error` + Retry, `.loading-state` +
  `.spinner`, `.empty-state`.
- **Design tokens** in `styles.css:3-26`: teal primary `#0f766e`, amber
  `#d97706` (hand-off/attention), red danger, Space Grotesk display / Inter
  body / IBM Plex Mono data; dark sidebar, light canvas, 12-16px radii,
  status always text+color.
- **Data loading pattern**: `useState` data/loading/error + `useEffect` keyed
  on `refreshKey`/filters + `AbortController`; global `refreshKey` +
  `reload()` (App.tsx:177, 214); toasts for user feedback.
- **Existing job UI to imitate**: `LoginJobWorkspace` modal (:1607) polls
  `GET /api/v1/login-jobs/{id}/progress` every 4s and renders progress bar,
  per-account states, and pause/resume/cancel/dispatch controls (:1614-1619).

**Consequence:** the newsletter module becomes new `activeView` values
(one parent "Newsletters" grouping is not how the rail works — each view is a
flat nav item; a single "Newsletters" nav item can own internal tabs for
Overview / Groups / Newsletters registry / Bulk Jobs / Activity, mirroring how
`InfrastructurePage` dispatches proxies/profiles/credentials/sessions sub-views,
App.tsx:389, 633-638).

---

## 8. Existing security architecture

- **Encryption:** Fernet (`SecretCipher`, security.py:16) with a key derived
  from `SECRET_KEY` (SHA-256 → urlsafe base64). Production requires a managed
  `SECRET_KEY`/KMS; `environment=production` guards (config.py:55-60).
- **Secrets separation:** `AccountSecret`/`CredentialRevision` hold encrypted
  payloads; operational metadata is separate. Credentials are decrypted only
  inside authorized execution boundaries (`_resolve_*`, service.py:1450+).
- **Employee auth:** scrypt-hashed passwords, opaque HttpOnly session cookie,
  hashed session tokens (auth.py). Gmail credentials are never reused as
  employee login secrets.
- **Authorization:** org + role + group-level checks (auth.py:92-138);
  `AUTH_REQUIRED`/`ADMIN_API_TOKEN` guard outside development.
- **No-secrets audit rule** (architecture.md:194; README security boundary):
  no passwords, recovery values, 2FA seeds, cookies, email bodies, or raw page
  text in logs/audit/API. Proxy credentials encrypted per-account and never
  returned.
- **Proxy isolation:** every account's browser is launched with its own stable
  proxy assignment (`_resolve_proxy_options`, service.py:1633).

---

## 9. Existing LLM architecture

**There is none.** No OpenAI/Anthropic/LiteLLM/langchain/Browser-Use dependency,
no LLM gateway, no AI endpoints in `main.py`. The browser automation is 100%
deterministic (rule-based locators + confidence heuristics, localized copy
tables).

**Consequence for the Goal's optional AI fallback (§15):** there is no existing
LLM gateway to reuse. Introducing AI would be a brand-new capability with a new
secret boundary and new deployments. The plan therefore treats AI as an
**optional, opt-in later phase** with a clean adapter seam, and default-to-
deterministic execution exactly as the Goal requires. No AI dependency is added
in the core phases, and any future AI assist must receive only placeholders —
never cookies/tokens/passwords/private mailbox contents.

---

## 10. Integration strategy

### REUSE (existing infrastructure, unchanged)

| Asset | Why reuse |
|---|---|
| `BrowserObserver` launch/teardown, page settle, CDP, persistent profiles (observer.py:217, 399, 631) | The account-isolation and browser boundary the Goal requires |
| `login_policy.py` page classification and `NEEDS_ATTENTION` handling | CAPTCHA/human-verification gating is mandatory |
| Gmail UI search (`_mail_search_input`, `_mail_subject_query`, `_mail_select_subject_rows`) | Existing mailbox-*access* mechanism (NO Gmail API) |
| `RedisQueue` / `RedisLocks` / `LoginWorker` dispatch, heartbeats, stale recovery (worker/*) | Existing queue; per-account lock gives isolation + concurrency control |
| `LoginJob`/`LoginJobItem` pattern + `login_job_progress` + pause/resume/cancel | Blueprint for `NewsletterJob`/`NewsletterTask` |
| `AuditEvent` table + no-secrets rule | Activity/audit (§21) |
| `SecretCipher` + `_resolve_proxy_options` + decryption-inside-execution | Security boundary |
| `notifications.publish` | Operator alerts on failed/manual-review work |
| `Auth: require_user / authorize_group / admin` | Tenant + role enforcement on every new route |
| `OperationDraft`/`CredentialRevision` versioning | Template for `NewsletterAutomationProfile` versioning |
| Frontend: `api()`, `refreshKey`, `ToastRegion`, modals, `StatusBadge`, `.factory-hero` patterns | Native look and behavior |

### ADAPT (existing machinery, extended for newsletter semantics)

- The **"mail workflow" observer handler** (`_handle_mail_tick`) → add
  `purpose="newsletter_confirm"`: after a subscription is submitted, search the
  mailbox for the newsletter's confirmation sender/subject, **open the matched
  message**, extract a confirmation URL, and **validate its domain** against the
  newsletter's allowlisted domains before navigating. If ambiguous or domain-
  mismatched → `MANUAL_REVIEW`. Never stores bodies; only the validated URL.
- The **per-purpose session lifecycle** (`_start_login_session_locked` purpose
  allowlist, service.py:2585; `_report_observer_event` terminal-event map,
  :1742) → add `newsletter` (signup) and `newsletter_confirm` (mailbox)
  purposes/terminal events.
- **`login_jobs` routing/UX conventions** → `newsletter-jobs` endpoints +
  a `NewsletterJobWorkspace` mirroring `LoginJobWorkspace`.
- The **account-group permission model** (EmployeeGroupAccess) → newsletter
  groups are managed under the same org + role gates; newsletter groups are
  *not* `AccountGroup` rows and cannot collide with fleet groups.

### EXTEND (existing entities gain related state)

- `GmailAccount` → relationship to a new `NewsletterSubscription` set (keeps
  account as the isolation unit; no new account-level columns required).
- `BrowserSession.metadata_json` → newsletter job/task references and profile
  version used in a session, mirroring `mail_workflow` metadata
  (service.py:2623).
- Worker `tick` (worker.py:69) → also dispatch `RUNNING` newsletter jobs via
  the same `dispatch_ready_items`-style function (shared account-lock makes
  cross-job profile contention impossible).

### NEWLY ADDED (no existing equivalent)

- **Entities** (`NewsletterGroup`, `Newsletter`, `NewsletterSubscription`,
  `NewsletterAutomationProfile`, `NewsletterJob`, `NewsletterTask`,
  `NewsletterTaskEvent`/activity) — schema follows existing conventions
  (`organization_id`, prefixed string IDs, state+index columns, unique
  constraints for idempotency).
- **`newsletter_form` module** — deterministic adaptive form detection:
  confidence-scored field/button candidates from Playwright locators over
  `input[type=email]`, name/id/placeholder/autocomplete/aria-label/label
  text, visibility/enabled state, DOM proximity to subscribe elements.
  Threshold behavior per Goal §13 (≥0.85 execute, 0.60–0.84 re-analyze,
  0.40–0.59 optional AI later, <0.40 manual review). Runs inside the existing
  observer loop as `_handle_newsletter_tick`; persisted findings feed profile
  versions.
- **Idempotency gate** — DB unique constraint
  `(organization_id, account_id, newsletter_id)` on `NewsletterSubscription`
  (concurrency-safe insert) **plus** a deterministic mailbox pre-check (search
  for the newsletter's known confirmation/sender signal) before any form
  submission. Already-subscribed → task `SKIPPED`.
- **Newsletter health** — per-newsletter derived metrics (success/failure/
  confirmation/retry/manual-review/confidence/profile-recovery rates)
  computed by job summary endpoint; a newsletter crossing health thresholds
  auto-flips to `TEST_REQUIRED` / `AUTOMATION_BROKEN`, per Goal §23.
- **Frontend newsletter area** — new `activeView` "Newsletters" nav item with
  internal tabs (Overview / Groups / Newsletters / Bulk Jobs / Activity),
  reusing tables, badges, modals, and the LoginJobWorkspace polling pattern.

### REJECTED

| Candidate | Reason |
|---|---|
| **Gmail API / IMAP** | Hard requirement — never. Existing mechanism is profile + Gmail UI automation (goal §0). |
| **Scrapling (D4Vinci)** | **SUPERSEDED BY `docs/Newsletter_integration/02`:** the approved production plan now integrates Scrapling as a **deterministic public-page discovery / parsing / adaptive-recovery layer only** (signup-page discovery, form/field/submit/consent detection, success-signal extraction, confirmation-domain hints, candidate-profile generation, rediscovery on breakage). It is explicitly **not** the execution layer: Playwright + persistent Gmail profiles remain authoritative for account operations (satisfies the account-isolation + credential boundaries). It never touches Gmail sessions, credentials, cookies, or CAPTCHA. See the update in §12. |
| **Browser Use / FormPilot / LiteLLM** | No LLM infra exists; deterministic + profiles come first. Optional AI later via adapter seam, not these tools by default. |
| **Celery / new queue** | Existing Redis queue + locks + worker already provides controlled concurrency, account isolation, retries, and stale recovery. |
| **New browser framework** | Playwright + persistent profiles already present and proven. |
| **Generic "Newsletter dashboard" UI** | Must look native: rail item + existing components, not a disconnected app. |

---

## 11. Domain model (adapted proposal)

```text
Organization ── NewsletterGroup (org-scoped; NOT AccountGroup)
   │                ├── Newsletter {id, group_id, name, website_url, signup_url,
   │                │                status, confirmation_required, created/updated}
   │                ├── NewsletterAutomationProfile {newsletter_id, version, config_json
   │                │                (email/submit/success/confirmation selectors),
   │                │                confidence, status, discovered_at, last_success_at}
   │                └── NewsletterSubscription {org, account_id, newsletter_id,
   │                        state, confirmed_at}   -- unique(org, account, newsletter)
   │
   ├── NewsletterJob {id, org, group_id, concurrency_limit, status,
   │        counts, retry_policy_json, auto_confirm, created_by, timestamps}
   │        └── NewsletterTask {job_id, org, account_id, newsletter_id,
   │                state, attempts, profile_version, worker_id, heartbeat_at,
   │                lock_key, last_error, timestamps}
   │                └── NewsletterTaskEvent {task_id, event, detail, result, ts}
   └── GmailAccount (existing) ← session drives signup/confirm in its profile
```

**Task states (adapted from Goal §7):**
`PENDING → QUEUED → PROCESSING (browser open) → SUBMITTED →
WAITING_CONFIRMATION → CONFIRMED`
with terminal/attention branches:
`SUBMITTED → SKIPPED` (already subscribed), `PROCESSING → SUBMITTED` without
confirmation when the newsletter sets none, and any attempt →
`FAILED` / `RETRY_PENDING` / `MANUAL_REVIEW` / `CANCELLED`. Confirmation exits:
`CONFIRMED` or `MANUAL_REVIEW` (ambiguous/not found/timeout/CAPTCHA).

**Job states:** `QUEUED → RUNNING → COMPLETED | PAUSED | CANCELLED | FAILED`
(command verbs expose pause/resume/cancel/retry-failed/skip-selected per §9,
reusing `update_login_job_state` semantics).

---

## 12. Priority-aligned implementation plan (adapted to the approved production plan)

Per `docs/Newsletter_integration/01` (11 phases), each ending with tests +
migration + isolation/security checks:

- **PHASE 0 (this doc)** — audit. ✔ done.
- **PHASE 1 — Schema:** migration + `NewsletterGroup`, `Newsletter`,
  `NewsletterAutomationProfile`, `NewsletterSubscription`,
  `NewsletterJob`, `NewsletterTask`, `NewsletterTaskEvent`; Pydantic
  schemas; org-scoped CRUD + authorization tests.
- **PHASE 2 — Dedicated page:** `activeView="newsletters"` nav item under
  Operations, internal tabs Overview / Registry / Groups / Automation /
  Bulk Jobs / Subscriptions / Activity, reusing existing design tokens.
- **PHASE 3 — Registry + Groups CRUD:** newsletter create/edit/disable,
  grouping, signup URL, confirmation config, allowlisted domains,
  sender/subject patterns.
- **PHASE 4 — Scrapling discovery:** isolated `newsletters/discovery.py`
  module (public fetch, signup-link discovery, form candidate extraction,
  field/submit/consent scoring, candidate profile, discovery endpoint +
  UI) per `docs/Newsletter_integration/02`. Profile lifecycle
  `DISCOVERED → CANDIDATE → TESTING → TEST_PASSED → ACTIVE`.
- **PHASE 5 — Single signup workflow:** `purpose="newsletter"` observer
  handler `_handle_newsletter_tick`, deterministic field/control detection
  with confidence thresholds, success verification, audit events,
  manual-review page-conditions.
- **PHASE 6 — Confirmation workflow:** `purpose="newsletter_confirm"` via
  existing profile + Gmail UI search, safe message open, allowlisted-link
  extraction, URL validation (HTTPS + allowlisted domain + expected
  context, no unknown redirects), CONFIRMED or MANUAL_REVIEW. **NO Gmail API.**
- **PHASE 7 — Idempotency:** subscription unique constraint, already-
  subscribed skip, retry safety.
- **PHASE 8 — Bulk jobs:** `NewsletterJob`/`NewsletterTask` dispatch on
  Redis queue + account locks, concurrency, heartbeat/stale recovery,
  pause/resume/cancel, retry-failed, skip-selected; live monitor UI
  mirroring `LoginJobWorkspace`.
- **PHASE 9 — Test mode + profile promotion:** single-account test,
  candidate profile, version promotion, rollback.
- **PHASE 10 — Health / adaptive recovery:** breakage thresholds →
  `TEST_REQUIRED` → `AUTOMATION_BROKEN`, Scrapling rediscovery,
  validation before promotion, bulk blocked while broken.
- **PHASE 11 — Hardening:** tenant isolation, locking, concurrency,
  no-secret logs, link validation, job/worker recovery, notifications,
  regression tests. AI stays behind a future adapter only.

### Rollback strategy

New tables are additive and share the existing `organization_id`/audit/queue
machinery. Rollback = drop the newsletter schema objects (reverse of migration
`0013`), remove the nav item, and disable `WORKER_ENABLED` newsletter dispatch;
no changes to account/profile/session/login code paths are required for
rollback except additive purposes in the observer/session lifecycle, which are
guarded by the existing `purpose` allowlist and `BROWSER_OBSERVER_ENABLED`
master switch.