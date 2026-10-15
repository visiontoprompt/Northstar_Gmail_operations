# Newsletter Automation — Dedicated SaaS Page UX & Frontend Specification

## 1. Goal

Newsletter Automation must be implemented as a **first-class dedicated page inside the existing Gmail Operations SaaS**.

It must use the same workspace shell, design system, permissions, loading states, error patterns, modals, toasts, and responsive behavior as the existing application.

The page should feel like a native module of the current SaaS, not an external tool.

---

# 2. Navigation

Add a main navigation item:

```text
Newsletters
```

Recommended placement:

```text
Operations
├── Sessions
├── Mail Workflow
└── Newsletters
```

Route/state example:

```text
activeView = "newsletters"
```

For future frontend modularization, it can become:

```text
/newsletters
```

---

# 3. Internal Tabs

Inside the page:

```text
Overview
Registry
Groups
Automation
Bulk Jobs
Subscriptions
Activity
```

Tabs should remain available without leaving the main Newsletters area.

---

# 4. Overview Tab

## Header

```text
Newsletter Automation
Discover, test, automate, and monitor newsletter subscriptions across Gmail account groups.
```

Primary actions:

```text
+ Add Newsletter
Discover Newsletter
Run Bulk Job
```

Secondary:

```text
View Manual Review
Refresh
```

---

## Summary Metrics

Recommended cards:

```text
Newsletters
Active
Test Required
Automation Broken

Subscriptions
Confirmed
Waiting Confirmation
Manual Review
Failed
```

Avoid excessive cards.

Use the existing summary-card pattern.

---

## Operational Sections

### Needs Attention

Show:

- broken automation
- newsletters awaiting tests
- manual-review tasks
- repeated confirmation failures
- failed rediscovery
- unavailable workers/browser profiles

### Recent Jobs

Show:

- job ID
- newsletter
- Gmail group
- status
- progress
- started
- completed
- action

### Health

Show factual metrics:

```text
Signup success
Confirmation success
Manual-review rate
Automation recovery events
```

---

# 5. Registry Tab

Table:

```text
Newsletter
Group
Website
Signup URL
Confirmation
Profile
Confidence
Status
Last Test
Last Success
Actions
```

Filters:

```text
Search
Group
Status
Confirmation required
Automation health
```

Row actions:

```text
View
Edit
Discover
Test
Run
Disable
```

---

# 6. Add Newsletter Flow

Use a modal or drawer consistent with the current SaaS.

Fields:

```text
Newsletter name
Newsletter group
Website URL
Signup URL optional
Confirmation required:
  Auto detect
  Yes
  No
```

Buttons:

```text
Cancel
Save
Save & Discover
```

Recommended default:

```text
Save & Discover
```

Discovery must still require validation before automation becomes active.

---

# 7. Discovery Experience

After clicking Discover:

```text
Discovery running...
```

Display:

```text
Website reachable             ✓
Newsletter page discovered    ✓
Email field detected          ✓
Submit control detected       ✓
Consent control               Not required
Success signal found          ✓
Confirmation likely           Yes

Confidence                    94%
```

Then:

```text
[Test with 1 account]
```

Do not offer unrestricted bulk execution before testing.

---

# 8. Automation Tab

Newsletter selector at top.

Show:

```text
Automation Status
Active Profile
Version
Confidence
Last Test
Last Success
Last Rediscovery
```

Configuration panel:

```text
Signup URL
Email selector
Submit selector
Consent selector
Success signals
Failure signals
```

Confirmation panel:

```text
Confirmation required
Sender patterns
Subject patterns
Allowed domains
```

Version panel:

```text
v5 ACTIVE
v4 SUPERSEDED
v3 SUPERSEDED
```

Actions:

```text
Rediscover
Test Current
View Candidate
Promote Candidate
Rollback
```

---

# 9. Test Workspace

Testing should be a dedicated modal/workspace.

Example:

```text
Test Newsletter Automation

Newsletter
Example Daily

Test account
account@example.com

[Start Test]
```

Live steps:

```text
1. Browser profile opened       PASS
2. Signup page loaded           PASS
3. Email field detected         PASS
4. Form submitted               PASS
5. Confirmation message found   PASS
6. Link validated               PASS
7. Subscription confirmed       PASS
```

Final state:

```text
TEST PASSED

Profile confidence: 96%

[Promote Profile]
[Close]
```

If not successful:

```text
TEST FAILED

Reason:
Confirmation URL did not match allowlisted domains.

[Manual Review]
[Rediscover]
```

---

# 10. Groups Tab

Newsletter groups are separate from Gmail account groups.

Table:

```text
Group
Description
Newsletters
Active
Broken
Actions
```

Actions:

```text
Create
Edit
Disable
View newsletters
```

---

# 11. Bulk Jobs Tab

Top actions:

```text
+ New Bulk Job
```

Job creation flow:

```text
Newsletter or Newsletter Group
        ↓
Gmail Account Group
        ↓
Account selection
        ↓
Concurrency
        ↓
Auto-confirm setting
        ↓
Review
        ↓
Start
```

Important:

Concurrency must be bounded by workspace policy.

---

## Live Job View

Use the existing login-job progress pattern.

Show:

```text
Job status
Progress
Concurrency
Current active profiles
Elapsed time
```

Counters:

```text
Queued
Processing
Submitted
Waiting confirmation
Confirmed
Skipped
Retrying
Failed
Manual review
```

Controls:

```text
Pause
Resume
Cancel
Retry Failed
Skip Selected
```

---

# 12. Subscriptions Tab

Table:

```text
Account
Account Group
Newsletter
Newsletter Group
State
Submitted
Confirmed
Last Checked
Last Error
```

Filters:

```text
Account
Account group
Newsletter
Newsletter group
State
Date
```

Possible states:

```text
PENDING
SUBMITTED
WAITING_CONFIRMATION
CONFIRMED
SKIPPED
FAILED
MANUAL_REVIEW
```

---

# 13. Activity Tab

Chronological events.

Filters:

```text
Newsletter
Account
Event
Result
Job
Date
```

Events:

```text
discovered
profile_created
profile_tested
profile_promoted
signup_started
signup_submitted
confirmation_waiting
confirmation_found
confirmation_opened
confirmed
skipped
retry
failed
manual_review
automation_broken
rediscovery_started
rediscovery_completed
```

---

# 14. Manual Review UX

Any unsupported state should surface clearly.

Examples:

```text
CAPTCHA
MFA
Unknown page
Confirmation ambiguity
Unexpected consent
Selector failure
Unvalidated URL
Browser startup failure
```

Manual review item:

```text
Newsletter
Account
Worker/Profile
Current state
Failure reason
Last observation
Allowed operator actions
```

Do not falsely label unresolved tasks successful.

---

# 15. Responsive Design

## Desktop

- full data tables
- persistent SaaS navigation
- multi-column summary areas
- job workspace side-by-side where useful

## Tablet

- condensed columns
- filters in drawer
- actions in overflow menus
- stacked health sections

## Mobile

Mobile is primarily for monitoring and basic controls.

Use:

- cards instead of wide tables where necessary
- bottom-sheet filters
- compact job controls
- clear status
- manual-review access
- no horizontal layout dependency

Avoid requiring complex automation-profile editing on very small screens if the interaction becomes unsafe or unusable.

---

# 16. Required States

Every tab/component should include:

```text
loading
empty
error
partial data
success
disabled
permission denied
manual review
offline worker
stale data
```

Reuse existing spinner, error, empty, toast, status, and modal components.

---

# 17. Accessibility

Requirements:

- labeled controls
- keyboard-accessible tabs
- keyboard-accessible menus
- semantic table markup
- visible focus states
- status represented by text + color
- destructive actions require confirmation
- sufficient contrast
- modal focus management

---

# 18. Frontend Refactor Recommendation

The existing frontend is highly centralized.

Before the Newsletters area becomes large, begin moving toward:

```text
frontend/src/

pages/
  newsletters/
    NewsletterPage.tsx
    NewsletterOverview.tsx
    NewsletterRegistry.tsx
    NewsletterGroups.tsx
    NewsletterAutomation.tsx
    NewsletterBulkJobs.tsx
    NewsletterSubscriptions.tsx
    NewsletterActivity.tsx

components/
  newsletters/

hooks/
api/
types/
```

The refactor should preserve the existing visual design.

---

# 19. API Areas

Recommended API namespace:

```text
/api/v1/newsletters
/api/v1/newsletter-groups
/api/v1/newsletter-profiles
/api/v1/newsletter-subscriptions
/api/v1/newsletter-jobs
/api/v1/newsletter-tasks
/api/v1/newsletter-discovery
```

Example actions:

```text
POST /newsletters
PATCH /newsletters/{id}
POST /newsletters/{id}/discover
POST /newsletters/{id}/test
POST /newsletters/{id}/rediscover

GET /newsletters/{id}/profiles
POST /newsletter-profiles/{id}/promote
POST /newsletter-profiles/{id}/rollback

POST /newsletter-jobs
POST /newsletter-jobs/{id}/start
POST /newsletter-jobs/{id}/pause
POST /newsletter-jobs/{id}/resume
POST /newsletter-jobs/{id}/cancel
GET  /newsletter-jobs/{id}/progress
```

All endpoints must remain organization/permission scoped.

---

# 20. Page Definition of Done

The Newsletters page is complete only when:

- it exists as a dedicated SaaS navigation destination
- newsletter CRUD works
- newsletter groups work
- discovery works
- testing works
- automation profiles are versioned
- signup works with persistent profiles
- confirmation works through Gmail UI
- duplicate subscription is prevented
- bulk jobs work
- pause/resume/cancel works
- activity/audit works
- manual review works
- responsive UI works
- permission boundaries work
- no-secret logging remains intact
- broken automation can be detected
- Scrapling rediscovery can generate a testable candidate profile
