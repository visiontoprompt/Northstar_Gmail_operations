# Gmail Operations Console — Current SaaS State Report

**Report date:** 2026-09-19
**Repository:** `G_workspace_Project`
**Report type:** Current-state product and architecture report
**Status vocabulary:**

- **Implemented:** represented in the current application and backend.
- **Implemented with review boundary:** automated where safe, with manual attention for Google challenges, consent, CAPTCHA, MFA, or unsupported states.
- **Preparation / limited:** the interface or data model exists, but execution is not universal or is intentionally incomplete.
- **Planned:** not currently represented as a complete product capability.

This document describes the repository as it exists now. It is not a production certification, security audit, or promise that external Google pages will remain unchanged.

## 1. Executive summary

The product is a browser-automation SaaS for operating a company-managed fleet of Gmail accounts. It combines:

- multi-user workspace access;
- account intake and grouping;
- encrypted Gmail credentials and credential history;
- browser profiles, proxies, and isolated sessions;
- bulk login jobs with concurrency controls;
- account-level Gmail operations;
- mail workflows, Google Alerts, add-ons, labels, security settings, and contacts;
- employee/group permissions;
- audit trails, issue tracking, and operational notifications.

The application is organized as a React/Vite frontend backed by FastAPI, a relational database, Redis-backed coordination, and Playwright-controlled persistent Chrome profiles. The application state and audit trail are authoritative; a browser page is treated as an automation surface, not as the source of truth.

The most important operational boundary is that Google may require human interaction. The system can recognize and pause for MFA, CAPTCHA, account recovery, security checks, consent, publisher authorization, or unknown pages. It does not claim to bypass those controls.

## 2. Product scope

### Primary use case

The console is designed for a team that manages a fleet of Gmail accounts. Operators can import accounts, organize them into groups, assign employee access, configure browser/proxy infrastructure, run controlled Gmail actions, and review the outcome of every account operation.

### Core principles visible in the implementation

1. **Account isolation:** each Gmail account uses its own persistent browser profile and, where configured, its own proxy assignment.
2. **Explicit scope:** actions can target selected accounts or the entire selected group.
3. **Controlled concurrency:** bulk jobs have per-operation and global limits.
4. **Auditable outcomes:** sessions, actions, errors, interruption, and manual-review states remain visible.
5. **Secret separation:** account secrets are stored separately from normal account metadata and are encrypted at rest.
6. **Human-in-the-loop safety:** Google security challenges and third-party consent are not silently accepted.
7. **Graceful interruption:** jobs and sessions can be paused, cancelled, stopped, or recovered from stale state.

## 3. Application navigation and page inventory

| Page | Current responsibility | Status |
|---|---|---|
| Overview | Workspace summary, account/session/job health, operational shortcuts | Implemented |
| Accounts | Searchable account registry, selection, account actions, account details | Implemented |
| Account groups | Create, edit, activate/deactivate, capacity, and account membership management | Implemented |
| Employees | Employee creation, roles, status, passwords, and group access | Implemented |
| Issues | Review queue for failed, interrupted, challenged, or attention-required work | Implemented |
| Account factory | Account intake, validation, deduplication, and bulk import | Implemented |
| Sessions | Latest session per account, activity history, filters, statuses, and session controls | Implemented |
| Proxies | Proxy inventory, testing, assignment, reassignment, metadata, and removal | Implemented |
| Credentials | Credential vault view, revisions, rotation flows, and sensitive-value controls | Implemented with review boundary |
| Browser profiles | Persistent profile inventory and account/profile status | Implemented |
| Settings | Workspace policy, operational limits, notification configuration, and account-management settings | Implemented |

The frontend currently uses a single workspace application with state-based page switching. The navigation is intentionally kept inside the SaaS shell rather than opening separate product windows for normal management pages.

## 4. Workspace authentication and employee access

### Workspace login

- First-run setup is supported when the application has no workspace administrator.
- Employees authenticate to the SaaS separately from the Gmail accounts managed by the SaaS.
- Workspace sessions use opaque, server-managed sessions delivered through an HttpOnly cookie.
- Login failures are returned as normal authentication errors rather than exposing credential details.
- Password reset request and confirmation flows are available.
- Workspace passwords are stored as scrypt hashes, not plaintext.
- Administrators can change or rotate an employee password.
- Logout invalidates the workspace session.

### Roles and group access

The current authorization model distinguishes at least these responsibilities:

- **Administrator / manager:** workspace administration, employee management, settings, groups, account fleet operations, and infrastructure controls.
- **Operator:** account operations within the groups assigned to the employee.
- **Auditor:** read-oriented access to accounts, sessions, issues, and audit information.

Group-level access can be assigned with viewer, operator, or manager permissions. This allows the administrator to decide which employees can see or operate which account groups. Suspended or deactivated employees cannot continue normal workspace work.

## 5. Accounts and account intake

### Account registry

The Accounts page supports:

- searchable and filterable account inventory;
- account status and authentication status;
- group membership and group moves;
- proxy/profile/session associations;
- selected-account actions;
- entire-group actions;
- account detail inspection;
- metadata editing;
- guarded archive/removal behavior;
- audit visibility for account changes and actions.

### Accepted account formats

Single and bulk intake accepts the following colon-delimited forms:

```text
email:password
email:password:recovery
email:password:recovery:2fa
```

The importer validates the records, identifies malformed rows, deduplicates within the organization, and reports accepted/rejected rows. Imported records are not automatically treated as successfully logged-in accounts; they remain ready for login until the browser session proves mailbox access.

### Secret handling

Account passwords, recovery values, and 2FA material are kept in the encrypted account-secret boundary. Normal account listings do not expose raw secret values. Sensitive values are only available through guarded credential actions and are intended for authorized users.

## 6. Groups and account organization

Groups are the main unit for employee access and bulk operations.

Current group capabilities include:

- create a group;
- edit group name and description;
- activate or deactivate a group;
- define a capacity limit;
- prevent capacity from being reduced below the active-account count;
- move accounts between groups;
- filter sessions, proxies, accounts, and bulk actions by group;
- run account operations for a selected group;
- close sessions for all accounts in a group;
- retain group changes in the audit trail.

Group capacity and operation limits can be governed from Settings.

## 7. Login automation and sessions

### Login flow

The login system uses a saved persistent Chrome profile for the account and launches a dedicated browser context. Where enabled, the observer can enter stored credentials on recognized Google sign-in pages. It then waits for an observed mailbox or another recognized terminal state.

The observer recognizes common localized Google states, including:

- sign-in and password pages;
- “Continue as…” variants in English, French, and Spanish;
- second-factor prompts;
- CAPTCHA and security-review pages;
- account-not-found states;
- mailbox pages;
- unknown or unsupported pages.

The following are intentionally not bypassed automatically:

- CAPTCHA;
- MFA that requires user action;
- Google security challenges;
- recovery or identity verification;
- unknown Google pages;
- unrelated consent or publisher authorization screens.

Those states are paused or marked for attention and remain auditable.

### Bulk login jobs

Bulk login jobs support:

- queueing selected accounts or a full group;
- per-job concurrency;
- global active-session limits;
- dispatch and dispatch-next controls;
- pause, resume, and cancel;
- progress reporting;
- per-account outcomes;
- retry/recovery handling for stale sessions;
- cleanup of stale profile locks and orphaned active sessions;
- preserving failed or interrupted results for review.

### Sessions page

The Sessions page can show the latest session for each account or the full activity history. It supports filtering by:

- group;
- account;
- action;
- outcome/status;
- active, completed, interrupted, failed, and review-required states.

Session controls include closing an individual session, closing all active sessions, and closing sessions for a group. Closing a session is a terminal control operation: the browser is closed, locks are released, and the outcome remains recorded.

## 8. Browser profiles

Browser profiles are persistent, account-backed Chrome profiles rather than fabricated UI rows. The profile inventory shows real profile metadata and its account relationship.

The profile model supports:

- persistent profile directory ownership;
- saved-profile status;
- account association;
- active/stale lock recovery;
- profile launch and cleanup;
- isolated pages and browser contexts;
- carrying forward Google cookies and session state where the saved profile remains valid.

The browser observer closes a profile when an account is conclusively nonexistent or when an operation reaches a terminal close state. Unsaved nonexistent-account profile directories are removed during cleanup.

## 9. Proxy management

### Input formats

The proxy system accepts:

```text
ip:port
username:password:ip:port
```

### Proxy inventory and lifecycle

The Proxies page supports:

- listing existing proxies;
- filtering by group and operational status;
- bulk selection;
- health/testing of one or many proxies;
- latency observation;
- provider and country/city detection after a successful health check;
- account assignment and reassignment;
- unassignment;
- editing proxy metadata;
- confirmed deletion;
- reviewing which accounts use each proxy.

Proxy credentials are encrypted and are supplied only to the isolated browser associated with the account assignment. The application does not invent geo or provider data: if detection fails, it reports that the observation is unavailable.

## 10. Credentials and credential operations

The Credentials page provides a controlled surface for sensitive account-management work.

Current capabilities include:

- choosing which credential fields to view or manage;
- guarded reveal and copy actions;
- credential revision history;
- password, recovery, and 2FA change planning;
- generated password material where configured;
- saving newly observed/entered credential material into the encrypted vault;
- replacing the old 2FA secret after a successful change;
- one-account credential change boundaries;
- confirmation, cancellation, and review states;
- audit entries without storing raw credentials in the audit event.

Google credential changes are browser workflows and may require a human when Google presents an identity, recovery, MFA, or security challenge. The application must not represent a plan as completed until the target page confirms the change.

The 2FA flow recognizes the authenticator setup sequence, including the “use/enter a setup key” path, localized labels, secret-key extraction, OTP generation, OTP entry, and verification. It is designed to stop safely instead of repeatedly deleting and recreating an authenticator when the page has already advanced.

## 11. Gmail and Google account operations

Operations are initiated from the account action rail. They can normally target selected accounts or the entire selected group.

### Contacts import

- Reusable contact files can be uploaded to a workspace library.
- CSV, vCard, and TXT-style sources are supported by the intake boundary.
- A profile-backed browser session opens Google Contacts and imports the selected file.
- The operation records account-level success, failure, or review status.

### Labels and markers

The Labels & Markers operation opens Gmail settings and attempts to:

- enable Gmail importance markers (“Show markers”);
- show the Spam system label;
- verify the resulting settings;
- record the outcome;
- close the session when the operation reaches a terminal state.

English, French, and Spanish labels are considered when locating controls.

### Enhanced Safe Browsing

The Security operation opens the Google Account Enhanced Safe Browsing page and attempts to enable the feature. It handles localized page text and the additional confirmation/activation step when available. Unknown pages, security challenges, or browser startup failures are not silently treated as success.

### Google apps / operation designers

The account action surface includes Google app-related actions and generic operation planning. Some generic operation-draft paths are preparation infrastructure rather than a guarantee that every possible Google service action is executable. The actual implemented workflows listed in this report are the authoritative scope.

## 12. Gmail add-ons

The Add-ons operation:

1. opens Gmail in the account’s saved session;
2. opens the right-side add-on rail;
3. locates the Get add-ons control;
4. opens the Google Workspace Marketplace;
5. selects up to three entries from the curated official Marketplace allowlist;
6. opens each listing and presses the localized install control;
7. handles the localized installation confirmation;
8. handles the Google authorization/“Continue” step where it is a normal add-on installation consent;
9. verifies the resulting state;
10. records the result and closes the session.

Publisher OAuth, billing, subscription, unrelated third-party consent, or unsupported authorization screens are deliberately moved to manual review rather than auto-approved.

The number of add-ons and bulk concurrency are bounded by the operation settings so a group operation does not open an uncontrolled number of browser sessions.

## 13. Google Alerts

Google Alerts is an implemented bulk workflow.

### Topic catalog

The backend contains a seeded catalog of at least 50 topics covering:

- global news and politics;
- technology and innovation;
- business and finance;
- health and wellness;
- entertainment and culture;
- sports;
- education and careers;
- lifestyle and sustainability.

### Operation behavior

- The employee selects the number of topics per account.
- The employee selects the allowed concurrent account count.
- Each account receives its own random, unique topic selection from the catalog.
- Topics are typed using paced/human-style input.
- The workflow waits for page suggestions/content to settle before looking for the create control.
- Create-alert controls are matched across localized text, including English, French, and Spanish variants.
- The workflow waits after submission and verifies that the alert was created before moving to the next topic.
- The result is recorded independently for each account and topic.
- Bulk work can be interrupted and reviewed.

## 14. Mail Workflow

Mail Workflow is the Gmail message-operation surface. It uses subject-based search and then performs the selected action on matching messages.

### Search and matching

- subject text is entered by the employee;
- matching can be contains or exact;
- the operation can be scoped to a folder/category such as Spam or Promotions;
- the account’s Gmail UI is used rather than assuming a backend API result;
- selection and toolbar state are checked before an action is considered successful.

### Available actions

#### Spam to Inbox

Find matching messages in Spam, select the matching message(s), and move them back to Inbox. The flow includes safeguards for Gmail’s spam-search and toolbar variations.

#### Promotions to Spam

Find matching messages in Promotions, select them, open the spam/report action, and handle the “Report spam or unsubscribe?” confirmation. The operation chooses the report-spam path rather than unsubscribe.

#### Reply

Find the matching message/thread, open the reply editor, type the selected response, and send it after verifying the editor and send control.

### Reply content and rotation

The employee can:

- enter a custom reply;
- choose a professional ready-made reply;
- choose random rotation;
- use the same selected reply for the job;
- use independently selected replies per account.

The ready-answer library includes professional acceptance, greeting, acknowledgement, thanks, clarification, follow-up, scheduling, refusal, holding response, and closing templates. The backend owns the random selection so bulk accounts do not all receive the same response by accident when rotation is enabled.

### Control and resilience

- workflow progress is shown per account;
- failures and manual-review states remain visible;
- active mail work can be interrupted;
- a stopped workflow does not silently report full completion;
- session closure is tied to terminal job state.

## 15. Notifications

Notification configuration is available from Settings.

### Telegram

The notification settings support configuring a Telegram bot token and destination/chat identifier. Telegram can be used for operational events such as:

- employee login events;
- group-level status reports;
- active/failed/interrupted account status;
- bulk job completion;
- manual attention requirements;
- important failures.

The application includes test/status-report actions so configuration can be checked without waiting for a production event.

### Email notifications

The application supports a choice between Resend and SMTP-style delivery. SMTP settings cover provider-style values such as host, port, username, password, and TLS behavior. Resend settings use the provider API-key/from-address boundary.

Email delivery is also used by password-reset flows. External delivery is dependent on valid provider configuration and network/provider availability; settings being present does not by itself prove that an email was delivered.

## 16. Settings and policy controls

Workspace settings provide operational policy controls including:

- default group capacity;
- maximum concurrent login work;
- maximum concurrent account operations;
- profile limits;
- observer polling cadence;
- session timeout;
- automatic browser close behavior;
- audit retention policy;
- notification provider and recipient configuration;
- Telegram and email test actions.

These controls are intended to prevent accidental fleet-wide over-dispatch and to make bulk behavior predictable.

## 17. Audit, issues, and operational reporting

The audit model records account, group, employee, proxy, credential-plan, session, and operation events. Audit events intentionally exclude raw passwords, recovery addresses, 2FA secrets, cookies, email bodies, and arbitrary page text.

The Issues page consolidates conditions that require attention, such as:

- failed operations;
- interrupted operations;
- Google MFA/CAPTCHA/security challenges;
- unknown pages;
- failed browser startup;
- account-not-found results;
- third-party authorization screens;
- proxy health or assignment failures.

The Sessions page and bulk-job progress views provide the operational status view, while the audit trail provides the durable historical record.

## 18. Backend and API surface

The backend is a FastAPI service under `backend/app`.

### Main API domains

| Domain | Representative capabilities |
|---|---|
| Authentication | setup, login, status, current employee, logout, reset request/confirmation, employee password update |
| Groups | list, create, edit, status, capacity, group access |
| Employees | list, create, edit, suspend/reactivate, deactivate, password, group permissions |
| Settings | read/update workspace policy and notification settings |
| Accounts | list, details, single intake, bulk preview/import, edit, group move, archive, operation drafts |
| Credentials | encrypted updates, reveal, copy boundary, rotation, revisions, change plans, confirm/cancel/start |
| Login jobs | create, start, pause, dispatch, resume, cancel, progress, stale-session recovery |
| Sessions | list, detail, events, observations, close, stop-active, close-group |
| Proxies | list, test, edit, assign, unassign, delete |
| Browser profiles | real profile inventory and status |
| Contacts | contact-file library and account import |
| Gmail operations | labels/markers, security, add-ons, alerts, mail workflow |
| Audit | filtered audit-event history |
| Notifications | provider test and status-report delivery |
| Health | service health/readiness information |

Interactive API documentation is available through the FastAPI application in development environments.

## 19. Architecture

```text
Employee browser
      │
      ▼
React/Vite workspace UI
      │ HTTP + HttpOnly workspace session
      ▼
FastAPI API and service layer
      ├── relational database (PostgreSQL or SQLite development fallback)
      ├── Redis queue/locks and worker coordination
      ├── encrypted account/notification secret storage
      └── Playwright + real Chrome observer
             ├── persistent browser profile per account
             ├── optional assigned proxy
             └── Gmail / Google UI operations
```

### Source-of-truth rule

Database state, job state, session state, and audit events are the application source of truth. Browser observations are inputs to state transitions. A browser tab being open is not equivalent to a successful operation.

### Worker and locking model

- Redis sorted sets coordinate queued work.
- Per-account locks prevent conflicting operations.
- Durable job-lock mirrors help recovery and inspection.
- In-process worker execution currently handles dispatch and operation lifecycle.
- Stale sessions and orphaned profile locks can be recovered.
- Pause, resume, cancel, and stop controls are reflected in job/session state.

## 20. Frontend implementation state

The frontend is currently a unified React/Vite console. It includes:

- a dark workspace sidebar and light operational canvas;
- shared cards, tables, badges, modals, forms, buttons, toasts, and status components;
- native accessible selects and labeled fields;
- responsive page sections for inventory, operations, and review queues;
- deliberate typography and visual tokens for headings, controls, data, and status;
- operation-specific progress and confirmation modals;
- guarded sensitive-value reveal/copy interactions;
- polling of session and job state where live progress is needed.

The main application is centralized in `frontend/src/App.tsx`, with shared styling in `frontend/src/styles.css`. This is effective for the current console but is also a maintainability consideration as the product continues to grow.

## 21. Data protection and safety boundaries

The current design separates:

- account metadata;
- encrypted account secrets;
- credential revisions;
- browser profile metadata and directories;
- proxy assignments and encrypted proxy credentials;
- browser sessions and observations;
- audit events;
- employee/workspace authentication sessions.

The following data should not appear in normal audit output or operational logs:

- Gmail passwords;
- recovery addresses or recovery secrets;
- 2FA setup secrets and OTPs;
- cookies;
- raw page text;
- email bodies;
- provider API keys or SMTP passwords.

The browser automation is consent-oriented. It can continue through normal UI controls and localized confirmation screens, but it is not designed to evade Google security controls or automatically approve unrelated third-party permissions.

## 22. Verification status

The repository has automated coverage across API and browser-observer behavior. The latest recorded verification included:

- backend API and browser-observer tests passing;
- frontend production build passing;
- whitespace/diff validation passing;
- coverage of login, groups, employee access, sessions, proxies, credentials, labels, security, add-ons, alerts, contacts, notifications, and mail workflow paths.

Automated tests validate application behavior and mocked/controlled browser states. They cannot guarantee that Google’s live UI, localized wording, anti-abuse behavior, account risk checks, proxy availability, or third-party Marketplace pages will remain unchanged.

## 23. Current limitations and operational caveats

These are important constraints, not hidden features:

1. **Google UI volatility:** selectors, page structure, wording, and consent flows can change without notice.
2. **Manual review is part of the design:** MFA, CAPTCHA, recovery, security review, unknown pages, and certain publisher consents may require a person.
3. **External delivery depends on configuration:** Telegram, Resend, and SMTP require valid credentials and reachable providers.
4. **Proxy observations are conditional:** geo/provider values are reported only when successfully observed; they are not inferred or fabricated.
5. **Browser health matters:** Chrome startup, profile locks, proxy reachability, and local display/browser process health affect execution.
6. **Operation completion requires verification:** an action being clicked is not sufficient; the backend should retain review status when the expected state cannot be confirmed.
7. **Development configuration exists:** local environments may use SQLite fallbacks, development CORS/configuration, and feature flags. Production deployment must explicitly configure secure database, Redis, secret storage, browser runtime, HTTPS, and authentication settings.
8. **Current worker topology is not a fully independent distributed worker fleet:** Redis coordination is present, but scaling and operations should be validated before treating it as a large production deployment.
9. **No Gmail API/IMAP replacement exists:** the current automation uses the Gmail/Google web UI through browser profiles.
10. **No universal Google-service abstraction exists:** generic operation drafts and designers exist, but only the workflows specifically listed as implemented should be considered executable product scope.

## 24. Capabilities not currently complete

### Newsletter Automation

There is no complete Newsletter Automation page documented as an active, end-to-end SaaS feature in the current application. A future newsletter feature should be treated as a separate product scope with its own data model, approval rules, sending provider, unsubscribe/compliance model, templates, scheduling, analytics, and worker lifecycle.

### LLM-driven automation

The current product does not require an LLM to choose actions. Topic selection and reply rotation are deterministic application features. A future AI layer would need explicit review, cost controls, prompt/data boundaries, and an approval policy before being introduced.

### Fully unattended Google security bypass

This is not a supported feature and should not be represented as one. Google security challenges remain a human-review boundary.

## 25. Recommended production-readiness checklist

Before calling the SaaS production-ready for a larger organization, validate:

- PostgreSQL and Redis production configuration;
- HTTPS and secure cookie settings;
- secret encryption key management and rotation;
- database backup and restore;
- browser-profile backup/retention policy;
- proxy credential access and rotation;
- worker deployment, health checks, and restart behavior;
- rate/concurrency limits appropriate for the Google accounts;
- Telegram/Resend/SMTP delivery with alerting for failed delivery;
- audit retention and export requirements;
- employee offboarding and immediate session revocation;
- Google UI regression tests in each supported language;
- manual-review runbooks for MFA, CAPTCHA, security checks, and consent;
- monitoring for orphaned Chrome processes and stale profile locks;
- incident response for credential exposure or provider compromise.

## 26. Final assessment

The SaaS is currently a broad Gmail operations console with a functioning workspace shell, account fleet management, browser-profile/proxy infrastructure, multi-user permissions, bulk job controls, Gmail UI workflows, notifications, and auditability.

Its strongest current areas are account organization, controlled browser sessions, credentials/profiles/proxies, group-based operations, session visibility, and explicit manual-review boundaries. Its principal risks are dependence on changing Google UI flows, the sensitivity of the managed account data, external provider configuration, and the need to validate worker/browser behavior under real production concurrency.

The accurate product position today is: **a feature-rich operational SaaS foundation with implemented end-to-end workflows, not a guarantee of fully unattended execution against every Google security or consent state.**
