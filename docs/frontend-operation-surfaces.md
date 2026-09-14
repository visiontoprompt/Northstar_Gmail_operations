# Frontend operation surfaces — Phase 1 handoff

This document describes the frontend contract added to the account detail view. It is intentionally a preparation layer: the current UI does not execute Google actions or pretend that a change was persisted.

## What is available now

Open an account from the registry to access:

- Edit account metadata: proxy label and proxy region are editable in the interaction shell; the Gmail address remains immutable here.
- Move group: choose another capacity-planned group. The selected group is not changed until a backend endpoint confirms the move.
- Delete account: requires typing the exact email. The UI does not delete the account or its profile.
- Login: opens the existing isolated profile-backed session flow. Automated credential entry, verification solving, and security bypass are outside this frontend milestone.
- Credentials: opens the existing encrypted credential-revision flow.
- Contacts, browser security, markers & labels, Google apps, send & reply, add-ons, and Google Alerts: each opens a workflow designer with operation-specific fields and a preparation-only notice.

## Required server contracts for a later agent

Implement these contracts behind the existing UI only after authorization, audit behavior, and tests are agreed:

| UI action | Suggested endpoint | Required behavior |
|---|---|---|
| Edit metadata | `PATCH /api/v1/accounts/{account_id}` | Validate metadata, preserve immutable email identity, write an audit event, return a safe account summary. |
| Move group | `POST /api/v1/accounts/{account_id}/group-move` | Validate destination capacity, lock the account, update group membership transactionally, write an audit event, return the updated summary. |
| Delete account | `DELETE /api/v1/accounts/{account_id}` | Require server-side confirmation, revoke/close owned sessions, apply the project retention policy to profile and secret records, write an audit event, and return no secrets. |
| Save operation draft | `POST /api/v1/accounts/{account_id}/operation-drafts` | Validate a typed operation payload, encrypt any sensitive configuration, version the draft, and return status plus audit-safe metadata only. |
| List operation drafts | `GET /api/v1/accounts/{account_id}/operation-drafts` | Return typed draft summaries and lifecycle state; never return passwords, recovery values, 2FA secrets, session cookies, or browser profile contents. |

The frontend should be changed from “connector pending” to a success state only after the corresponding response is accepted and the registry/detail data is reloaded. Errors should remain inline or appear as an error toast; do not show optimistic success for destructive or security-sensitive actions.

## Operation payload guidance

Use a discriminated payload such as `{ type, version, configuration }`, with allowlisted fields per operation. Contacts should reference an uploaded/imported asset rather than embedding uncontrolled files in an account record. Mail workflows should be drafts until a separately authorized send action exists. Alerts, add-ons, labels, app configuration, and browser-security preferences should each have explicit reviewable fields and an audit trail.

Do not put secrets in query strings, browser storage, audit metadata, UI notifications, error messages, or client-side analytics. Keep login and other Google interactions behind the approved isolated-session boundary, with human/verification attention states represented explicitly rather than bypassed.

## Verification checklist for the next agent

1. Add request/response schemas and migration tests before wiring the buttons.
2. Add authorization checks for organization and group ownership on every endpoint.
3. Test duplicate identity, full destination group, stale account version, repeated delete, and concurrent move requests.
4. Test that secrets are absent from responses, logs, audit events, and client storage.
5. Test keyboard navigation, destructive confirmation, loading/error states, and reload-after-success in the frontend.
6. Keep the Phase 1 visual hierarchy: dark navigation rail, group context, readiness boundary, semantic account table, and operation cards inside account detail.

