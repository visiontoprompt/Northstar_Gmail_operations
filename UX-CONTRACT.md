# UX Contract — Phase 1 Account Onboarding

This contract covers the Phase 1 admin product surface. Visual intent lives in [DESIGN.md](DESIGN.md). Business architecture evidence comes from the approved project conversation and the architecture reference supplied with it.

## Canonical owners

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Form | Shared form field patterns in `frontend/src/App.tsx` | UX-CONTRACT.md | create / import | Inline errors and API tests |
| Select/Listbox | Native select | UX-CONTRACT.md + DESIGN.md | native | Keyboard and popup review |
| Search | Shared debounced registry behavior | UX-CONTRACT.md | query / clear | 300ms debounce and stale-request test |
| Table | Semantic account table | API list contract | page / responsive stacked records | Table semantics and API tests |
| Newsletter bulk-run report | Bulk Subscribe job results dialog | Persisted newsletter job and task outcomes | completion popup / reopen from the operation row | Job transition, report loading/error, task outcome review |
| Registry actions | Registry action rail | This contract + account operation APIs | selected / entire group | Scope, disabled, partial-failure review |
| Toast | `ToastRegion` | UX-CONTRACT.md | success / error / info | Live-region interaction review |
| CRUD | Account service + registry flow | API contract + UX-CONTRACT.md | create / import / edit / move / delete shell | Inline errors, destructive confirmation, and API tests |
| Proxy inventory | Proxies route + bulk import / proxy group manager dialogs | Proxy inventory and proxy group APIs | import / assign / edit / unassign / remove / group create / rename / member edit / delete | Inline row outcomes, safe group deletion, encrypted persistence, audit, authorization, and API tests |
| Session hand-off | Account detail session action + session workspace | Browser session API | open / authenticated / verification required / closed | Session lifecycle API and browser smoke test |
| Browser desktop | Admin-only Browser desktop workspace + Nginx-authenticated noVNC surface | Account groups, account/session APIs, existing operation dispatchers, login-job dispatcher, and shared server X display | group-scoped launcher / status-filtered scope / selected or filtered operation scope / collapsible application and account navigation / resizable optional inspector / inline queue / multi-window session switcher / per-session close confirmation / full-screen viewer / disabled runtime | Admin authorization on status, focus endpoint, and WebSocket handshake; operations receive the exact displayed scope; non-login operations require authenticated accounts; one durable session per account; closing one session leaves other sessions and the shared desktop intact; panel preferences persist; sessions survive viewer navigation |
| Group directory | Account groups route + group dialogs | `GET/POST/PATCH/DELETE /api/v1/groups` | list / create / edit / admin-only delete / open accounts | API response, capacity, dependency conflicts, permission, audit, and refresh review |
| Workspace pages | Overview, Issues, Account factory, Employees, Settings route owners | Existing read APIs and runtime policy | live summary / exception queue / onboarding entry / access model / configuration reference | Loading, empty, error, and navigation review |
| Scrollbar | Global application stylesheet | DESIGN.md | standard / stable overflow | Computed style and visual review |

## Account onboarding flow

1. Admin opens the Account Registry and chooses a group in the header or page filter.
2. `Add account` opens the single-account form. `Import accounts` opens the bulk preview/import form.
3. The admin can enter `email:password`, `email:password:recovery`, or `email:password:recovery:2fa` in a masked textarea. Raw lines are never shown after preview/import completion.
4. Preview validates syntax and reports row-level errors, duplicates, and masked metadata only.
5. Import is pessimistic and idempotency-aware at the service boundary: the server validates again, encrypts the secret payload, persists metadata and an audit event, and returns only safe summaries.
6. Successful records are stored with `lifecycle_status=SETUP_REQUIRED` and `readiness_status=READY_FOR_LOGIN`. This does not mean Google authentication succeeded.
7. The registry refreshes, keeps the selected group/search context, and announces `Account added` or an exact partial-import count.
8. Account detail can open one profile-backed login session. The session workspace exposes the current hand-off state and explicit actions to record a human-completed login or a verification requirement, then close the session.

## Registry action rail

The Account Registry owns the action surface. Login, credential revision,
contacts, browser security, markers and labels, Google apps, send and reply,
add-ons, Google Alerts, metadata edit, group move, and archive are displayed in
the registry action rail above the table rather than as the primary actions of
the account detail modal.

- `Selected` applies to the checked rows in the current registry view.
- `Entire group` applies to every account in the selected group, not only the
  currently loaded page.
- Login uses the existing controlled batch flow and its concurrency setting.
- Contacts opens the Contact File Library. A reusable CSV, VCF, or TXT file is
  uploaded once, selected from the library, and then sent through the
  authenticated account profile to `contacts.google.com`.
- Non-login operations save one audited plan per account until their execution
  worker is available. `Labels & Markers` is the approved exception: it runs
  immediately through the saved authenticated profile boundary.
- Credential revision remains limited to exactly one selected account because
  credential values are account-specific and security-sensitive.
- Archive is confirmed with an explicit bulk confirmation and each server-side
  archive remains independently auditable. Partial failures remain visible and
  retryable.

## State vocabulary

Onboarding owns `NEW`, `SETUP_REQUIRED`, `READY_FOR_LOGIN`, `DUPLICATE`, `INVALID`, and `ARCHIVED`. The session hand-off owns `LOGIN_REQUIRED`, `AUTHENTICATED`, `VERIFICATION_REQUIRED`, `NEEDS_ATTENTION`, and `CLOSED`; these are session/browser observations, not proof that a healthy Gmail worker is running. `ACCOUNT_NOT_FOUND` is terminal: the account becomes `NONEXISTENT`, the browser closes, the profile lock is released, and the unsaved local profile directory is removed.

When Google presents a CAPTCHA, phone verification, recovery-email verification, or another manual security check, the account session keeps its browser open and records the specific reason. Recovery-email fields are never populated with the login address. Ordinary form actions pause until that challenge clears. The operator can complete it in the same browser; the observer then resumes ordinary sign-in or the pending operation when Google presents a safe page. A login batch item remains running and occupies its concurrency slot while verification is pending. It changes to succeeded only after the same session reaches authenticated Gmail or Google Account evidence.

On the Ubuntu single-server deployment, the persistent Playwright Chrome windows run headed on one virtual X display. The administrator-only Browser desktop is the primary session workspace: operators choose an account group, filter by authentication/verification/browser state, select accounts or apply an operation to all filtered accounts, and set concurrency for login batches. Existing operation designers receive the exact scope selected in this workspace; non-login operations are blocked unless every target is authenticated. The application navigation and account launcher can collapse independently, while a resizable, optional inspector shows account/session state and can close only the selected browser after an explicit confirmation. Session queues and per-account close actions never terminate unrelated account windows or the shared desktop. A session switcher focuses the selected account's existing Chrome window through the window manager; it never creates a duplicate session. All account windows share the desktop. Navigating away from or closing the viewer does not close account sessions. VNC and WebSocket services bind to loopback; Nginx checks the employee session cookie and administrator role for viewer assets and every new WebSocket handshake. The desktop is a view/control surface for existing sessions, not a replacement automation runtime.

With an enabled workspace 2Captcha key, a visible reCAPTCHA v2 or standalone Turnstile widget can be submitted once to the provider while the browser remains open. A returned token is passed to the live widget; the observer still requires ordinary page evidence before continuing. Unsupported CAPTCHAs, phone challenges, provider failures, and missing keys remain manual handoffs.

## Validation and sensitive values

- Browser profile compaction runs after the persistent Chrome context closes and is also available from Accounts for selected or all operator-accessible accounts. It removes only exact Chromium cache directories; account cookies, site storage, Service Workers, preferences, and unknown data remain untouched. A locked, active, or still-running profile is skipped and reported. Manual progress counts processed accounts and reports actual cache bytes removed.
- Accounts shows the on-disk size for each profile and lets operators export up to 100 selected, closed profiles as one ZIP mapped by account email for the existing profile importer. Export includes Chrome user data (including cookies and site data), excludes transient browser lock files and filesystem links, uses a bounded private temporary archive, and is audited without logging profile contents. The export is a byte-preserving transfer; it does not guarantee cross-host cookie decryption or Google session validity.

- Backup creation opens a selection dialog before any work starts. The database and encrypted credentials are required; browser profiles and deployment configuration are optional and selected by default. The archive records the selected components. Restoring an archive without profiles preserves the server's live profile directory; restoring one without deployment configuration does not create an empty staged configuration directory. A full safety snapshot is made before either restore.

- Email is normalized case-insensitively for deduplication.
- Bulk rows require two to four non-empty colon-separated fields. The password is required; recovery and 2FA are optional and only appear in the preview when supplied.
- Password, recovery, and 2FA values are masked by default and never appear in URLs, logs, browser storage, audit metadata, toast text, or response payloads.
- The textarea is `resize: none`, has explicit help text, and clears after a successful bulk import.
- Proxy inventory accepts one connection per line as `ip:port` or `username:password:ip:port`, with optional `| Label | Region` metadata. Raw connection lines are masked while being entered, never echoed in results, logs, audit metadata, or API responses, and clear after a successful import. Valid endpoints are encrypted as unassigned inventory; an existing endpoint is reported as a duplicate and is never overwritten by an import.
- Proxy inventory supports a selected-endpoint bulk test. The proxy page keeps the selection and tests endpoints in a bounded set of parallel one-endpoint requests, showing a live completed count, in-flight count, progress bar, and each endpoint's result as its request finishes. Duplicate test activation and conflicting proxy edits are disabled while the run is active. The final inline summary remains available until dismissed; a transport failure stops new checks, lets in-flight checks finish, and states how many endpoints were not tested rather than presenting an incomplete run as a pass.
- Proxy distribution has two explicit modes: equal spread across the selected healthy endpoints for one account group, or a maximum accounts-per-proxy limit that counts assignments outside the group. Both validate the complete plan and reject locked account profiles before changing assignments.
- Proxy inventory endpoints can be organized into named proxy groups, filtered by group, and moved or ungrouped in bulk. Distribution previews the approximate per-endpoint share for the selected account group; applying a distribution updates only that group, with one proxy assignment per account.
- VPS providers is an infrastructure page. OVHcloud credentials are entered in Settings, stored encrypted, and never returned by the API. The page lists live VPS and Public Cloud instance status, shows partial API permission errors, and requires a confirmation step before start or stop. Accepted power requests remain pending at OVHcloud; the page refreshes status instead of claiming an immediate state change. Missing Floating IP recovery configuration does not block an OVH Start request, but the durable recovery result remains failed and proxy readiness unverified with a visible reason. A running configured instance exposes Verify/recover proxies so an operator can start verification after manual OVH start or after correcting protected server credentials; duplicate runs are rejected.

## Navigation, feedback, and recovery

- Registry is the owning list. Successful create/import returns to it.
- Overview is a live summary and links to the registry, groups, issues, and
  sessions. Account groups is the organizational owner for creating groups and
  opening a group-scoped registry view; new groups default to the 300-account
  operating target and creation does not mutate existing accounts.
- Account factory reuses the canonical single/bulk onboarding surfaces. Issues
  is derived from current account, proxy, and profile status. Employees owns a
  live organization-scoped directory: create/invite, edit, suspend, reactivate,
  and deactivate are all server-backed and audited; deactivation is soft so
  the employee record remains available for evidence. Employees also owns the
  explicit employee-to-group access map: each assignment is `VIEWER`,
  `OPERATOR`, or `MANAGER`, and replacement is audited. Employee sessions are
  authenticated separately from Gmail account credentials; operators see only
  assigned groups and administrators control the directory. Settings owns persisted
  workspace policy controls for group capacity, dispatch limits, profile
  limits, observer cadence, session timeout, automatic close behavior, and
  audit retention. Secrets, API authentication, database/Redis URLs, and
  process-level browser flags remain deployment-managed and read-only here.
- API validation failures stay in the form with inline corrections; network/server failures stay in the form with retry.
- Newsletter bulk jobs open a results dialog when an observed active run reaches a terminal state. The operation row keeps a report icon beside `Details` so the persisted job and per-account/per-newsletter outcomes can be reopened after navigation or dismissal. The report distinguishes successful, skipped, failed, cancelled, and review-needed tasks from the overall job status; it does not infer success from submission alone.
- The newsletter signup dialog accepts an optional publisher password for the selected account and newsletter. It is encrypted on that subscription record and reused for later signup or confirmation runs; it is never copied from the Gmail login credential or included in the account identity. The registry displays a signup score derived from completed account outcomes and can sort the lowest scores first for manual website-group review. Unknown outcomes remain unscored, and sorting does not change group membership or disable a site.
- Search/filter changes reset the page to 1 and preserve state in URL parameters when the frontend is connected to a router.
- Registry status filters are horizontal buttons with API-backed counts for the current group and search scope. They include All, Ready for login, Setup required, Authenticated, Needs attention, and Nonexistent. The page-size choices are 25 (default), 50, 100, and All, with explicit previous/next controls when paginated.
- Rows open a right-side account detail drawer that re-fetches the account, sessions, and audit events when opened and refreshes the live record while it remains visible. Overview, Sessions, and Audit are functional tabs; the drawer shows current server state rather than relying on the registry row snapshot. At narrow widths it becomes a full-screen surface.
- Account detail is a focused read surface: phase, login, Gmail observation, lifecycle, vault, proxy, profile, metadata, session history, and audit history each have a dedicated place. Registry actions remain in the registry action rail. The action rail exposes operations after accounts are selected or the entire group scope is selected; when account selection is nonempty, the rail stays docked near the viewport bottom while the table scrolls.
- Contact files are reusable organization assets. The library displays filename,
  format, contact count, size, and usage time without returning file contents;
  imports reference the selected asset by ID.
- Account detail exposes server-backed edit metadata, move group, and guarded archive controls. Archive requires operator access to the account's group, records the authenticated employee as actor, and reloads the registry after success. Failed bulk archives identify the account and server-provided reason; partial completion refreshes the registry.
- The account registry shows measured on-disk profile bytes beside a meter with a 100 MB reference. The reference is a visual comparison, not an enforced storage limit; profiles over it display their actual size and an over-reference percentage.
- Account detail exposes preparation-only operation designers for contacts, browser security, Google apps, and send and reply. `Labels & Markers` runs the approved Gmail settings operation through the saved authenticated profile; it enables Importance markers, shows Spam under System labels, verifies both changes, audits the result, and closes the session. `Add-ons` runs the bounded Marketplace operation through the saved authenticated profile: it opens Gmail, opens Get add-ons, selects up to three allowlisted Marketplace listings per account, approves the localized Marketplace installation confirmation, verifies installation, audits the result, and closes the session. `Google Alerts` runs through the saved authenticated profile: it opens `https://www.google.com/alerts`, selects the configured number of unique topics independently per account from the organization’s 50-topic catalog, submits and verifies them one by one, audits the selected topic count, and closes the session. Publisher OAuth, billing, subscription, and unrelated account-consent prompts pause the affected session for operator approval rather than being accepted automatically.
- Mail Workflow uses the saved authenticated Gmail profile and requires an explicit subject and action. Move and reply actions may run as bounded bulk jobs; reply uses a prepared approved answer. Confirmation review has two explicit modes and an explicit Gmail message-state filter, defaulting to unread: `Open one email for employee review` is single-account: it submits the subject search, waits for Gmail to leave the previous mailbox route, finds and opens one matching email in the selected unread/read state, waits for Gmail to render the message controls, records aggregate visible button/link and confirmation-candidate counts, then records `MAIL_WORKFLOW_REVIEW_READY` and leaves the session open for the assigned employee. `Scan matching emails into a review queue` is a bounded bulk review: it captures the matching thread routes once from the live search result list, opens one captured thread at a time because Gmail removes an opened thread from a read-state filtered result, and never treats a mailbox route or a transient empty result list as a settled search. For each opened thread it records only aggregate control counts, activates the highest ranked confirmation control inside the message body, follows the resulting page through a bounded number of steps, and records the per-message outcome as confirmed, failed, or needing review; a sign-in or blocked host stops that message instead of continuing. The session closes after the configured per-account limit or the available matches are exhausted, and only reports success when every opened email was confirmed. A completed scan means the review pass completed; it never means a subscription was confirmed. Neither mode retains link targets or message content beyond the run.
- Profile-backed operation sessions poll their server state while the workspace is open. When an automated operation reaches a terminal result, the browser is closed, the profile lock is released, and the session workspace closes after the latest state is refreshed. Failed operations remain visible in session history and audit events.
- Archive is exposed only through the guarded Delete action because it affects account, secret, session, and profile records.
- Registry status filters include API-counted Verification required and Never logged in states. Account tags are editable for selected accounts and shown as compact chips in the account row. Right-clicking a row opens Login, Edit, and Archive actions for that account.
- GoDaddy DNS is an administrator-only Infrastructure route. It stores multiple named production or OTE API credential pairs encrypted at rest, lists domains per credential, and displays the selected domain's DNS zone. Adding records is separate from replacing or deleting a record set; type/name replacement preserves other RRsets, and NS/SOA records are read-only in the UI. Credential and zone mutations are audited without storing API secrets.
- Proxy groups are managed from the Proxies inventory. Group rename updates endpoint membership and OVH group links together. `Manage endpoints` filters the inventory to a group so selected endpoints can be moved to another group or ungrouped. Deleting a group removes its OVH group links and clears only the endpoint group label; inventory, encrypted proxy connections, and account assignments remain intact. Every mutation is authorized, audited, and refreshed from the API.

## Accessibility and responsive behavior

Target WCAG 2.2 AA. Use semantic labels, visible focus, `aria-invalid`/`aria-describedby`, live-region status, keyboard-accessible modals, and a mobile stacked-record view rather than shrinking all table columns. Reduced motion removes entrance transforms.

## Route titles

The registry owns `Account registry — Gmail Operations`. The account detail shell owns `Account detail — Gmail Operations`.
The workspace routes own `Overview — Gmail Operations`, `Groups — Gmail Operations`,
`Employees — Gmail Operations`, `Issues — Gmail Operations`, `Factory — Gmail
Operations`, and `Settings — Gmail Operations`.
