# UX Contract — Phase 1 Account Onboarding

This contract covers the Phase 1 admin product surface. Visual intent lives in [DESIGN.md](DESIGN.md). Business architecture evidence comes from the approved project conversation and the architecture reference supplied with it.

## Canonical owners

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Form | Shared form field patterns in `frontend/src/App.tsx` | UX-CONTRACT.md | create / import | Inline errors and API tests |
| Select/Listbox | Native select | UX-CONTRACT.md + DESIGN.md | native | Keyboard and popup review |
| Search | Shared debounced registry behavior | UX-CONTRACT.md | query / clear | 300ms debounce and stale-request test |
| Table | Semantic account table | API list contract | page / responsive stacked records | Table semantics and API tests |
| Registry actions | Registry action rail | This contract + account operation APIs | selected / entire group | Scope, disabled, partial-failure review |
| Toast | `ToastRegion` | UX-CONTRACT.md | success / error / info | Live-region interaction review |
| CRUD | Account service + registry flow | API contract + UX-CONTRACT.md | create / import / edit / move / delete shell | Inline errors, destructive confirmation, and API tests |
| Session hand-off | Account detail session action + session workspace | Browser session API | open / authenticated / verification required / closed | Session lifecycle API and browser smoke test |
| Scrollbar | Global application stylesheet | DESIGN.md | standard / stable overflow | Computed style and visual review |

## Account onboarding flow

1. Admin opens the Account Registry and chooses a group in the header or page filter.
2. `Add account` opens the single-account form. `Import accounts` opens the bulk preview/import form.
3. The admin can enter `email:password:recovery:2fa` in a masked textarea. Raw lines are never shown after preview/import completion.
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
- Non-login operations save one audited plan per account until their execution
  worker is available.
- Credential revision remains limited to exactly one selected account because
  credential values are account-specific and security-sensitive.
- Archive is confirmed with an explicit bulk confirmation and each server-side
  archive remains independently auditable. Partial failures remain visible and
  retryable.

## State vocabulary

Onboarding owns `NEW`, `SETUP_REQUIRED`, `READY_FOR_LOGIN`, `DUPLICATE`, `INVALID`, and `ARCHIVED`. The session hand-off owns `LOGIN_REQUIRED`, `AUTHENTICATED`, `VERIFICATION_REQUIRED`, `NEEDS_ATTENTION`, and `CLOSED`; these are session/browser observations, not proof that a healthy Gmail worker is running.

## Validation and sensitive values

- Email is normalized case-insensitively for deduplication.
- Bulk rows require exactly four non-empty colon-separated fields.
- Password, recovery, and 2FA values are masked by default and never appear in URLs, logs, browser storage, audit metadata, toast text, or response payloads.
- The textarea is `resize: none`, has explicit help text, and clears after a successful bulk import.

## Navigation, feedback, and recovery

- Registry is the owning list. Successful create/import returns to it.
- API validation failures stay in the form with inline corrections; network/server failures stay in the form with retry.
- Search/filter changes reset the page to 1 and preserve state in URL parameters when the frontend is connected to a router.
- Rows link to an account detail shell. Detail tabs are non-functional placeholders until their phase is implemented, but the overview clearly marks them as future work.
- Account detail exposes server-backed edit metadata, move group, and guarded archive controls. The API validates, audits, and reloads the registry after success.
- Account detail also exposes preparation-only operation designers for contacts, browser security, markers and labels, Google apps, send and reply, add-ons, and Google Alerts. Each designer saves a versioned, non-secret plan; no external action occurs when a plan is saved.
- Archive is exposed only through the guarded Delete action because it affects account, secret, session, and profile records.

## Accessibility and responsive behavior

Target WCAG 2.2 AA. Use semantic labels, visible focus, `aria-invalid`/`aria-describedby`, live-region status, keyboard-accessible modals, and a mobile stacked-record view rather than shrinking all table columns. Reduced motion removes entrance transforms.

## Route titles

The registry owns `Account registry — Gmail Operations`. The account detail shell owns `Account detail — Gmail Operations`.
