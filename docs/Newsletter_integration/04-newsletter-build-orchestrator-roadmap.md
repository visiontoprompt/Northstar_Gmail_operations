# Newsletter Automation — Luna Build Orchestrator Roadmap

## Purpose

This file is the authoritative execution roadmap for continuing development of the Newsletter Automation module.

It complements:

- `01-newsletter-automation-production-plan.md`
- `02-newsletter-scrapling-integration.md`
- `03-newsletter-saas-page-ux-spec.md`

The three specification files define architecture, Scrapling boundaries, and UX.

This file defines the **order of implementation**, the **scope of each phase**, the **verification gate**, and what must remain deferred until later phases.

Luna should use this file as the primary build sequence.

---

# Core Rules

1. Do not skip phases unless a later phase is technically required to complete an earlier one.
2. Do not implement fake or placeholder success behavior.
3. Do not claim an operation is implemented unless it executes and verifies the expected result.
4. Preserve existing Gmail Operations architecture.
5. Reuse existing FastAPI, PostgreSQL, Redis, locks, jobs, BrowserObserver, Playwright, persistent browser profiles, account proxies, permissions, audit events, and notifications.
6. No Gmail API.
7. No IMAP.
8. AI/LLM integration is postponed.
9. Scrapling is approved only for public newsletter discovery, parsing, candidate profile generation, and adaptive recovery.
10. Playwright + the account's persistent profile remain the execution layer.
11. CAPTCHA, MFA, Google security challenges, unknown pages, and unsupported consent must go to manual review.
12. Preserve no-secret logging and audit rules.
13. Every phase ends with tests and a completion report before continuing.

---

# Current Status

## PHASE 1 — COMPLETE

Newsletter schema and backend registry foundation.

Implemented:

- newsletter_groups
- newsletters
- newsletter_automation_profiles
- newsletter_subscriptions
- newsletter_jobs
- newsletter_tasks
- newsletter_task_events
- SQLAlchemy models
- schemas
- registry CRUD
- profile versioning
- subscription idempotency
- job definitions
- tenant isolation
- account-group authorization
- audit events
- secret-safe responses
- tests

Recorded verification:

- Full backend suite: 207 passed, 1 skipped
- Newsletter tests: 13 passed
- Frontend production build: passed
- Python compilation: passed
- diff/whitespace validation: passed

Do not redo Phase 1 unless a later integration phase exposes a real defect.

---

# PHASE 2 — Dedicated Newsletter SaaS Page

## Goal

Create the dedicated **Newsletters** page inside the existing SaaS.

Navigation:

```text
Operations
├── Sessions
├── Mail Workflow
└── Newsletters
```

Internal Newsletter tabs:

```text
Overview
Registry
Groups
Automation
Bulk Jobs
Subscriptions
Activity
```

## Implement

- dedicated Newsletters navigation item
- Newsletter workspace
- internal tabs
- Overview using Phase 1 data
- Registry
- Add/Edit Newsletter
- Groups management
- Automation profile version viewer
- supported profile management
- job definition list/create only if Phase 1 API supports it cleanly
- subscription registry
- newsletter-related activity/audit view
- loading/error/empty states
- permissions
- responsive behavior
- accessibility
- newsletter-specific frontend modules/API helpers

## Do Not Implement Yet

- Scrapling
- Discover
- Save & Discover
- newsletter execution
- browser signup
- Gmail confirmation
- bulk execution
- live progress
- Redis newsletter dispatch
- adaptive recovery
- AI

## Phase Gate

Must pass:

- frontend production build
- existing frontend checks
- full backend pytest
- newsletter backend tests
- diff validation

Must return exact files changed and deferred scope.

---

# PHASE 3 — Registry / Groups / Profile Management Stabilization

## Goal

Make the non-execution Newsletter product surface complete and stable before introducing discovery.

## Implement

- fix Phase 2 UI/API friction
- improve Registry filters/search
- validate newsletter/group state transitions
- profile version detail presentation
- promotion/rejection/rollback UX
- newsletter status presentation
- activity filtering
- permission correctness
- clean empty states
- consistent error handling
- endpoint/schema cleanup only where required
- responsive refinement

## Important

This phase should remain small.

Do not add execution logic.

## Phase Gate

- Newsletter registry is production-usable as a data-management surface
- Groups are stable
- Profile versioning is understandable and auditable
- all current tests pass
- no fake execution UI

---

# PHASE 4 — Scrapling Discovery

## Goal

Integrate Scrapling as a bounded public-page discovery layer.

## Implement

Create discovery service, recommended location:

```text
backend/app/newsletters/discovery.py
```

or a repository-native equivalent.

Capabilities:

- discover newsletter signup pages
- inspect likely newsletter URLs
- find email fields
- find submit controls
- find consent controls
- detect success/failure signals
- collect confirmation-domain hints
- score candidates
- generate candidate NewsletterAutomationProfile versions

UI:

- Discover action
- discovery progress
- ranked candidate signup URLs
- candidate form details
- candidate confidence
- create candidate profile

## Scrapling Boundary

Scrapling may handle:

```text
public website discovery
parsing
adaptive selector recovery
candidate selector generation
```

Scrapling must NOT handle:

```text
Gmail sessions
Gmail credentials
persistent Gmail profiles
CAPTCHA bypass
MFA bypass
Google security bypass
mailbox execution
```

## Phase Gate

- discovery produces bounded candidate profile data
- no profile becomes ACTIVE merely because Scrapling discovered it
- candidate version is persisted
- discovery errors are handled
- no secrets are exposed
- tests pass

---

# PHASE 5 — Single-Account Newsletter Signup

## Goal

Execute a newsletter signup using the existing Gmail account's persistent browser profile.

Add:

```text
purpose="newsletter"
```

## Workflow

```text
Acquire account lock
        ↓
Launch persistent profile
        ↓
Open signup URL
        ↓
Load selected automation profile
        ↓
Verify page state
        ↓
Locate email field
        ↓
Locate consent if required
        ↓
Locate submit control
        ↓
Fill Gmail address
        ↓
Submit
        ↓
Verify success/result
        ↓
SUBMITTED
```

## Implement

- BrowserObserver newsletter purpose
- deterministic locator verification
- bounded form candidate scoring
- success/failure verification
- browser/session events
- audit events
- manual-review boundary
- safe browser close/cleanup
- single-account test endpoint/UI integration

## Do Not Implement Yet

- bulk dispatch
- confirmation email handling
- AI

## Phase Gate

One account can be tested safely from UI/API and result is factual:

```text
SUBMITTED
SKIPPED
FAILED
MANUAL_REVIEW
```

No success based only on a click.

---

# PHASE 6 — Gmail Confirmation

## Goal

Confirm subscriptions using the existing Gmail web UI in the same account profile.

Add:

```text
purpose="newsletter_confirm"
```

## Workflow

```text
SUBMITTED
   ↓
WAITING_CONFIRMATION
   ↓
Open Gmail
   ↓
Search expected sender/subject/newsletter signal
   ↓
Open matched message
   ↓
Extract candidate confirmation link
   ↓
Validate URL/domain
   ↓
Open allowed URL
   ↓
Verify confirmation
   ↓
CONFIRMED
```

## Implement

- reuse Gmail UI search machinery
- open matched message safely
- extract only needed link information
- never persist raw email body
- HTTPS validation
- allowlisted confirmation domain validation
- redirect validation where applicable
- manual review on ambiguity/mismatch
- confirmation result verification

## Phase Gate

Single-account test can complete signup + confirmation safely and verifiably.

---

# PHASE 7 — Subscription Reconciliation and Idempotency

## Goal

Make retries and repeated jobs safe.

## Implement

- enforce existing unique subscription constraint
- check subscription state before signup
- already-subscribed handling
- deterministic mailbox pre-check where useful
- reconcile SUBMITTED / WAITING_CONFIRMATION / CONFIRMED states
- safe retry rules
- retry timeout behavior
- stale confirmation recovery
- duplicate prevention
- audit/events

## Expected Result

Repeated requests must not repeatedly subscribe the same account.

Use factual skip states, e.g.:

```text
SKIPPED_ALREADY_SUBSCRIBED
```

---

# PHASE 8 — Bulk Newsletter Jobs

## Goal

Reuse existing Redis/job/lock infrastructure for newsletter jobs.

## Implement

- job start
- dispatch
- per-account NewsletterTask creation
- Redis queue integration
- account/profile locking
- heartbeat
- stale recovery
- concurrency limits
- pause
- resume
- cancel
- retry failed
- skip selected
- progress endpoint
- per-account outcomes
- job summary
- live Bulk Jobs UI

## Important

Do not introduce Celery or a second queue framework.

Use existing Redis infrastructure.

## Phase Gate

A selected Gmail Account Group can run a bounded newsletter job with accurate totals for queued, processing, submitted, waiting confirmation, confirmed, skipped, retrying, failed, manual review, and cancelled states.

---

# PHASE 9 — Test / Promote / Rollback Workflow

## Goal

Make automation profiles safe to deploy.

## Profile Lifecycle

```text
DISCOVERED
   ↓
CANDIDATE
   ↓
TESTING
   ↓
TEST_PASSED
   ↓
ACTIVE
```

Failure states:

```text
TEST_FAILED
REJECTED
MANUAL_REVIEW
```

Old production version:

```text
SUPERSEDED
```

## Implement

- Test with 1 account
- live test step UI
- test result storage
- profile promotion
- supersede prior active version
- rollback
- test failure details
- candidate rejection
- test audit events

## Rule

No newly discovered profile becomes ACTIVE without successful validation.

---

# PHASE 10 — Breakage Detection + Scrapling Adaptive Recovery

## Goal

Automatically detect newsletter automation breakage and produce a safe replacement candidate.

## Track

- signup success rate
- confirmation success rate
- selector failure rate
- manual-review rate
- retry rate
- recovery rate
- rediscovery rate

## States

```text
ACTIVE
   ↓
TEST_REQUIRED
   ↓
AUTOMATION_BROKEN
```

## Recovery Flow

```text
Repeated deterministic failure
       ↓
Scrapling rediscovery
       ↓
candidate vNext
       ↓
single-account test
       ↓
PASS → promote
FAIL → manual review
```

## Important

Never replace the active profile from a single transient network failure.

Use thresholds and repeated factual failures.

---

# PHASE 11 — Production Hardening

## Goal

Prepare the Newsletter module for larger production use.

## Validate

- tenant isolation
- permissions
- no-secret logging
- migrations
- database indexes
- Redis queue recovery
- stale locks
- browser cleanup
- orphaned Chrome processes
- profile persistence
- URL allowlists
- redirect validation
- manual-review behavior
- job recovery after restart
- concurrency limits
- notifications
- responsive UI
- accessibility
- regression coverage
- supported languages where existing Gmail automation already localizes controls
- error sanitization
- audit retention
- operational documentation

## Notifications

Use existing infrastructure for:

- job completion
- job failure
- automation broken
- repeated selector failure
- manual review
- confirmation failure spike
- browser failure
- recovery failure

---

# FUTURE PHASE — Distributed Worker Fleet

Do not mix this into Newsletter phases unless required.

The current SaaS already has Redis coordination but may later evolve from in-process worker execution into distributed RDP worker services.

Future architecture:

```text
Central Web SaaS
      ↓
Redis / Worker API
      ↓
RDP Worker Fleet
      ↓
Persistent local browser profiles
```

Newsletter implementation should avoid decisions that make this future separation difficult.

---

# FUTURE PHASE — AI / LLM

AI is postponed.

Do not introduce Qwen, Ollama, OpenAI, OpenRouter, LangChain, PydanticAI, smolagents, or any other LLM dependency during current Newsletter phases.

Future AI, if added, should remain a bounded decision/advisory layer.

It must not replace deterministic execution, security checks, or manual-review rules.

---

# Orchestrator Behavior for Luna

At the start of every phase:

1. Read this roadmap.
2. Read relevant sections of the three Newsletter specification files.
3. Inspect the current repository implementation.
4. Announce the exact phase scope being implemented.
5. Do not ask for confirmation if the phase is already authorized.
6. Implement only that phase plus minimal required fixes.
7. Run tests.
8. Return a completion report.
9. Stop after the phase unless explicitly instructed to continue.

---

# Required Phase Completion Report

Every phase must end with:

## Phase

Example:

```text
PHASE 4 — Scrapling Discovery
```

## Implemented

Exact behavior added.

## Files Changed

Exact paths.

## Database Changes

If any.

## API Changes

Actual routes added/changed.

## Frontend Changes

Actual views/components added.

## Tests

Exact command results.

## Security / Isolation

What was verified.

## Deferred

Everything intentionally left for later phases.

## Problems / Deviations

Anything not implemented exactly as specified and why.

## Next Phase

Name only.

Do not automatically implement the next phase unless instructed.

---

# Current Next Phase

Current implementation status:

```text
PHASE 1 — COMPLETE
```

Next:

```text
PHASE 2 — Dedicated Newsletter SaaS Page
```

Use the detailed Phase 2 directive supplied by the orchestrator together with this roadmap.
