# Newsletter Automation — Production Implementation Plan

## 1. Purpose

This document defines the implementation plan for adding a production-grade **Newsletter Automation** module to the existing Gmail Operations SaaS.

The new capability must be implemented as an extension of the current architecture, not as a replacement for it.

The existing SaaS already provides the critical foundations that must be reused:

- FastAPI backend
- PostgreSQL production database
- Redis queue and locking infrastructure
- Playwright-controlled real Chrome/Chromium
- one persistent browser profile per Gmail account
- per-account proxy assignments
- browser/session lifecycle management
- per-account locks
- bulk jobs and concurrency controls
- audit trails
- permissions and organization/group boundaries
- manual-review handling
- notifications
- Gmail UI-based mail workflows

The Newsletter module MUST NOT introduce Gmail API or IMAP.

AI/LLM integration is postponed to a future phase.

For now, the newsletter system must remain deterministic and use Scrapling only where it improves public-page discovery, parsing, and adaptive recovery.

---

# 2. Product Placement

Newsletter Automation will have its **own dedicated page inside the existing SaaS**.

It must appear as a primary navigation item in the existing application shell.

Recommended navigation:

```text
Workspace
├── Overview
├── Accounts
├── Account Groups
├── Employees
├── Issues

Operations
├── Sessions
├── Mail Workflow
├── Newsletters        ← NEW DEDICATED PAGE

Infrastructure
├── Workers
├── Browser Profiles
├── Proxies
├── Credentials

Settings
```

The Newsletters page should remain visually and structurally native to the existing SaaS.

It must NOT be implemented as a disconnected external application.

---

# 3. Newsletter Page Structure

The dedicated Newsletters page should contain internal tabs:

```text
Newsletters

├── Overview
├── Registry
├── Groups
├── Automation
├── Bulk Jobs
├── Subscriptions
└── Activity
```

## 3.1 Overview

Shows the operational health of the entire newsletter system.

Recommended metrics:

```text
Total newsletters
Active newsletters
Need testing
Automation broken
Disabled newsletters

Total subscriptions
Confirmed subscriptions
Waiting confirmation
Manual review
Failed
Skipped
```

Also show:

- latest bulk jobs
- newsletters currently failing
- newsletters requiring re-test
- current automation health
- recent activity
- current active browser sessions
- manual-review queue

---

## 3.2 Registry

Registry of newsletter sources.

Columns should include:

- Newsletter name
- Newsletter group
- Website
- Signup URL
- Confirmation required
- Automation status
- Active automation profile version
- Confidence
- Last successful test
- Last discovery
- Last successful subscription
- Health status
- Actions

Supported actions:

- View
- Edit
- Discover
- Test
- Disable
- Re-enable
- View versions
- View subscriptions
- Run bulk job

---

## 3.3 Groups

Newsletter groups are separate from Gmail account groups.

Example groups:

```text
Technology
News
Finance
Sports
Health
Business
Lifestyle
Entertainment
```

A newsletter group organizes newsletters only.

It must NOT reuse AccountGroup records.

---

## 3.4 Automation

Shows the automation configuration for a selected newsletter.

Example:

```text
Newsletter: Example Daily

Signup URL
Email selector
Submit selector
Consent selector
Success indicators
Failure indicators

Confirmation required
Confirmation sender patterns
Confirmation subject patterns
Allowed confirmation domains

Profile version
Confidence
Last discovery
Last successful run
```

The operator must be able to:

- inspect automation profile versions
- compare previous versions
- test a candidate profile
- promote a tested profile
- rollback to a previous working profile
- trigger rediscovery

---

## 3.5 Bulk Jobs

Use the existing bulk-job UX pattern.

Show:

- job status
- selected newsletter/group
- target Gmail account group
- total tasks
- queued
- processing
- submitted
- waiting confirmation
- confirmed
- skipped
- retrying
- failed
- manual review
- cancelled

Controls:

- Start
- Pause
- Resume
- Cancel
- Retry failed
- Skip selected
- View task details

---

## 3.6 Subscriptions

A factual registry of Gmail account-to-newsletter relationships.

Columns:

- Gmail account
- Account group
- Newsletter
- Newsletter group
- Current subscription state
- Submitted at
- Confirmed at
- Last checked
- Worker/session
- Last error
- Actions

---

## 3.7 Activity

Chronological operational events.

Examples:

```text
newsletter.discovered
newsletter.profile.created
newsletter.profile.tested
newsletter.profile.promoted
newsletter.signup.started
newsletter.signup.submitted
newsletter.confirmation.waiting
newsletter.confirmation.found
newsletter.confirmation.opened
newsletter.confirmed
newsletter.skipped
newsletter.retry
newsletter.manual_review
newsletter.failed
newsletter.automation_broken
```

No passwords, cookies, 2FA values, raw email bodies, or arbitrary raw DOM/page text may be stored in activity records.

---

# 4. Core Architecture

The system should use the following separation:

```text
PUBLIC NEWSLETTER WEBSITE
          │
          ▼
      Scrapling
          │
          ▼
Discovery / Parsing / Adaptive Recovery
          │
          ▼
NewsletterAutomationProfile
          │
          ▼
Existing Persistent Browser Infrastructure
          │
          ▼
Playwright BrowserObserver
          │
          ▼
Newsletter Signup
          │
          ▼
Gmail UI Confirmation Workflow
          │
          ▼
NewsletterSubscription
```

Scrapling is not the Gmail executor.

Playwright with the existing account profile remains the execution layer.

---

# 5. Existing Infrastructure to Reuse

The following existing systems must be reused.

## Browser infrastructure

Reuse:

- BrowserObserver
- persistent Chrome profile
- profile locks
- existing launch/teardown lifecycle
- page settle logic
- browser session records
- manual-review boundaries

Each account remains:

```text
1 Gmail account
    =
1 persistent browser profile
    +
1 proxy assignment
    +
1 lock
    +
controlled browser sessions
```

---

## Queue infrastructure

Reuse:

- Redis queue
- Redis account locks
- durable job lock mirror
- job heartbeats
- stale lock recovery
- session recovery
- concurrency limits
- pause/resume/cancel semantics

No new queue framework should be introduced.

---

## Security

Reuse:

- organization boundaries
- employee permissions
- encrypted secrets
- audit events
- no-secrets logging policy
- notification infrastructure

---

# 6. Domain Model

Add the following entities.

## NewsletterGroup

```text
id
organization_id
name
description
status
created_at
updated_at
```

---

## Newsletter

```text
id
organization_id
group_id

name
website_url
signup_url

status
confirmation_required

allowed_confirmation_domains
sender_patterns
subject_patterns

discovery_status
last_discovered_at
last_verified_at

created_at
updated_at
```

Recommended status values:

```text
ACTIVE
TEST_REQUIRED
AUTOMATION_BROKEN
DISABLED
```

---

## NewsletterAutomationProfile

```text
id
organization_id
newsletter_id

version
status

configuration_json

confidence

discovered_at
tested_at
promoted_at

last_success_at
last_failure_at

created_at
updated_at
```

Configuration may include:

```text
signup_url
email_selector
submit_selector
consent_selector

success_signals
failure_signals

confirmation_sender_patterns
confirmation_subject_patterns
allowed_confirmation_domains
```

Profiles are versioned.

Never silently overwrite a working profile.

---

## NewsletterSubscription

```text
id
organization_id
account_id
newsletter_id

state

submitted_at
confirmed_at
last_checked_at

last_error

created_at
updated_at
```

Add a unique constraint on:

```text
organization_id
account_id
newsletter_id
```

This is required for idempotency.

---

## NewsletterJob

```text
id
organization_id
newsletter_id
newsletter_group_id
account_group_id

status
concurrency_limit

retry_policy_json
auto_confirm

created_by
created_at
started_at
completed_at
```

---

## NewsletterTask

```text
id
organization_id
job_id

account_id
newsletter_id

state
attempts

profile_version
worker_id
browser_session_id

heartbeat_at
lock_key

last_error

created_at
updated_at
```

---

## NewsletterTaskEvent

```text
id
organization_id
task_id

event
result
detail

created_at
```

Event detail must remain bounded and secret-safe.

---

# 7. Newsletter Task State Machine

Recommended lifecycle:

```text
PENDING
   ↓
QUEUED
   ↓
PROCESSING
   ↓
SUBMITTED
   ↓
WAITING_CONFIRMATION
   ↓
CONFIRMED
```

Other possible branches:

```text
SKIPPED
RETRY_PENDING
FAILED
MANUAL_REVIEW
CANCELLED
```

Examples:

```text
PROCESSING → SUBMITTED
SUBMITTED → WAITING_CONFIRMATION
WAITING_CONFIRMATION → CONFIRMED

PROCESSING → SKIPPED
PROCESSING → RETRY_PENDING
PROCESSING → MANUAL_REVIEW
PROCESSING → FAILED
```

No task should be considered successful only because a button was clicked.

Expected result must be verified.

---

# 8. Signup Workflow

Add:

```text
purpose="newsletter"
```

to the existing observer/session purpose model.

Workflow:

```text
Acquire account/profile lock
        ↓
Launch persistent Chrome profile
        ↓
Open newsletter signup URL
        ↓
Load active automation profile
        ↓
Verify page state
        ↓
Locate email field
        ↓
Locate required consent
        ↓
Locate submit control
        ↓
Fill account email
        ↓
Submit
        ↓
Wait for stable response
        ↓
Verify success
        ↓
SUBMITTED
```

If page or controls cannot be recognized confidently:

```text
rediscovery/recovery attempt
        ↓
if still unresolved
        ↓
MANUAL_REVIEW
```

---

# 9. Deterministic Form Detection

The live browser must verify automation-profile elements before using them.

Candidate scoring should consider:

```text
input[type=email]
autocomplete=email
name contains email
id contains email
label text
aria-label
placeholder
DOM proximity to signup container
visibility
enabled state
```

Recommended decision behavior:

```text
>= 0.85
execute

0.60 - 0.84
retry deterministic candidates / re-analyze

< 0.60
Scrapling rediscovery or manual review
```

Because AI is postponed, no score range should automatically invoke an LLM.

---

# 10. Gmail Confirmation Workflow

Add:

```text
purpose="newsletter_confirm"
```

Reuse the existing Gmail UI mail workflow.

Flow:

```text
SUBMITTED
   ↓
WAITING_CONFIRMATION
   ↓
Open Gmail in same account profile
   ↓
Search expected sender / subject / newsletter signals
   ↓
Find candidate confirmation message
   ↓
Open matched message
   ↓
Extract candidate confirmation links
   ↓
Validate link
   ↓
Open approved confirmation link
   ↓
Verify confirmation page
   ↓
CONFIRMED
```

Raw email bodies must not be persisted.

---

# 11. Confirmation URL Security

A confirmation URL must pass all required validation.

Minimum requirements:

```text
HTTPS
+
hostname/domain belongs to newsletter allowlist
+
expected confirmation context
+
no unknown redirect chain
```

If ambiguous:

```text
MANUAL_REVIEW
```

Never open arbitrary email links.

---

# 12. Subscription Idempotency

Before signup:

```text
Check NewsletterSubscription
```

If a confirmed/active relationship already exists:

```text
SKIPPED_ALREADY_SUBSCRIBED
```

The system may also perform a deterministic Gmail search for known sender/subject signals before submission when useful.

Never repeatedly subscribe the same account due to job retries.

---

# 13. Testing Mode

Every new or changed newsletter automation should first pass a controlled test.

Example:

```text
Newsletter: Example Daily

[Test with 1 account]
```

Test report:

```text
Page reachable                 PASS
Email field detected           PASS
Submit detected                PASS
Signup submission              PASS
Confirmation message found     PASS
Confirmation link validated    PASS
Confirmation completed         PASS

Confidence: 0.96
```

Only after successful testing should the automation profile be eligible for bulk use.

Recommended states:

```text
UNTESTED
TESTING
TEST_PASSED
TEST_FAILED
ACTIVE
```

---

# 14. Health and Breakage Detection

Track newsletter-level metrics:

```text
signup success rate
confirmation success rate
failure rate
manual-review rate
selector recovery rate
rediscovery rate
retry rate
```

If a previously stable newsletter begins failing:

```text
ACTIVE
   ↓
TEST_REQUIRED
   ↓
AUTOMATION_BROKEN
```

Bulk scheduling should be blocked for broken automation until a replacement profile is tested.

---

# 15. Notifications

Reuse the existing notification infrastructure.

Notify operators for:

- bulk job completion
- bulk job failure
- newsletter marked AUTOMATION_BROKEN
- repeated selector failure
- manual review
- confirmation failure spike
- worker/browser failure
- profile recovery failure

---

# 16. AI Policy

AI integration is explicitly postponed.

The current newsletter feature must work without any LLM.

Future AI must be introduced only behind an adapter boundary and must not replace:

- existing account isolation
- Playwright execution
- policy validation
- manual-review handling
- security controls
- auditability

---

# 17. Recommended Implementation Sequence

## Phase 1 — Schema

Implement:

- NewsletterGroup
- Newsletter
- NewsletterAutomationProfile
- NewsletterSubscription
- NewsletterJob
- NewsletterTask
- NewsletterTaskEvent
- migrations
- schemas
- CRUD
- authorization tests

---

## Phase 2 — Dedicated Newsletter Page

Add the new SaaS navigation item:

```text
Newsletters
```

Implement internal tabs:

```text
Overview
Registry
Groups
Automation
Bulk Jobs
Subscriptions
Activity
```

Reuse existing design tokens and shared components.

---

## Phase 3 — Registry and Groups

Implement:

- create/edit/delete/disable newsletter
- newsletter grouping
- signup URL management
- confirmation configuration
- allowlisted domains
- sender and subject patterns

---

## Phase 4 — Scrapling Discovery

Integrate Scrapling for public newsletter website discovery.

See separate Scrapling document.

---

## Phase 5 — Single Signup Workflow

Implement:

```text
purpose="newsletter"
```

Use:

- existing persistent account profile
- existing proxy
- existing observer
- deterministic form detection
- verification
- audit events

---

## Phase 6 — Confirmation Workflow

Implement:

```text
purpose="newsletter_confirm"
```

Reuse Gmail UI search.

Implement:

- confirmation discovery
- safe message opening
- allowed-link extraction
- URL validation
- confirmation result verification

---

## Phase 7 — Idempotency

Implement:

- subscription unique constraint
- duplicate prevention
- already-subscribed skip
- retry safety

---

## Phase 8 — Bulk Jobs

Reuse Redis and account locks.

Add:

- NewsletterJob
- NewsletterTask dispatch
- concurrency limits
- heartbeat
- stale recovery
- pause/resume/cancel
- retry failed
- skip selected

---

## Phase 9 — Testing and Profile Promotion

Implement:

- single-account test
- candidate automation profile
- version promotion
- rollback

---

## Phase 10 — Health / Adaptive Recovery

Implement:

- breakage thresholds
- TEST_REQUIRED
- AUTOMATION_BROKEN
- Scrapling rediscovery
- validation before promotion

---

## Phase 11 — Production Hardening

Validate:

- tenant isolation
- account/profile locking
- concurrency
- worker recovery
- no-secret logs
- link validation
- browser-profile persistence
- job recovery
- migrations
- UI states
- notifications
- regression tests

---

# 18. Final Architecture

```text
                   WEB SAAS
                      │
         Dedicated Newsletters Page
                      │
                      ▼
               FastAPI Backend
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   PostgreSQL       Redis       Scrapling
        │             │       Discovery Layer
        │             │             │
        │             └──────┐      │
        │                    │      │
        ▼                    ▼      ▼
 Newsletter Jobs      Existing Worker
                            │
                            ▼
                  Persistent Browser Profile
                            │
                            ▼
                      Playwright
                            │
                  ┌─────────┴─────────┐
                  │                   │
          Newsletter Website       Gmail UI
                  │                   │
                  └─────────┬─────────┘
                            ▼
                  NewsletterSubscription
```

This preserves the architecture already implemented while adding a dedicated, extensible Newsletter Automation product area.
