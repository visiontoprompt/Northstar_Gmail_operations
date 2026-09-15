# Frontend operation surfaces — Phase 1 handoff

This document describes the frontend and backend contract for the account detail view. Account administration is server-backed; Google operation cards save typed plans only and do not execute external actions.

## What is available now

Open an account from the registry to access:

- Edit account metadata: proxy label and proxy region are editable; the Gmail address remains immutable. The API validates and audits the update.
- Move group: choose another capacity-planned group. The API checks destination capacity and audits the transactional move.
- Delete account: requires typing the exact email. The API archives the account, closes active sessions, and leaves profile/secrets subject to retention policy.
- Login: opens the existing isolated profile-backed session flow. Automated credential entry, verification solving, and security bypass are outside this frontend milestone.
- Credentials: opens the existing encrypted credential-revision flow.
- Contacts: the registry action opens the reusable Contact File Library. An administrator uploads a CSV, VCF, or TXT asset once, selects a ready file, and starts the import for one account, selected accounts, or the current group. The backend stores the asset outside account metadata and opens the account's isolated profile at `https://contacts.google.com/` for the observer-driven upload.
- Browser security, markers & labels, Google apps, send & reply, add-ons, and Google Alerts: each opens a workflow designer and saves a versioned, non-secret operation draft. Saving a draft does not execute the Google action.

## Server contracts now implemented

These endpoints now back the UI. Production authentication/authorization should replace the current demo actor before deployment:

| UI action | Endpoint | Required behavior |
|---|---|---|
| Edit metadata | `PATCH /api/v1/accounts/{account_id}` | Validate metadata, preserve immutable email identity, write an audit event, return a safe account summary. |
| Move group | `POST /api/v1/accounts/{account_id}/group-move` | Validate destination capacity, lock the account, update group membership transactionally, write an audit event, return the updated summary. |
| Delete account | `DELETE /api/v1/accounts/{account_id}` | Require server-side confirmation, revoke/close owned sessions, apply the project retention policy to profile and secret records, write an audit event, and return no secrets. |
| Save operation draft | `POST /api/v1/accounts/{account_id}/operation-drafts` | Validate a typed non-secret operation payload, version the draft, and return status plus audit-safe metadata only. |
| List operation drafts | `GET /api/v1/accounts/{account_id}/operation-drafts` | Return typed draft summaries and lifecycle state; never return passwords, recovery values, 2FA secrets, session cookies, or browser profile contents. |
| Upload contact file | `POST /api/v1/contact-files` | Validate a CSV/VCF/TXT asset, store it under the organization contact library, return metadata and a contact count, and never return file contents. |
| List contact files | `GET /api/v1/contact-files` | Return reusable file metadata only. |
| Start contact import | `POST /api/v1/accounts/{account_id}/contacts-import` | Accept a `file_id`, require an authenticated profile, open a `contacts.google.com` session, and record the file reference in session metadata. |

The frontend shows a success state only after the corresponding response is accepted. Errors remain inline or appear as an error toast; destructive and security-sensitive actions do not use optimistic success.

## Operation payload guidance

Use a discriminated payload such as `{ type, version, configuration }`, with allowlisted fields per operation. Contacts should reference an uploaded/imported asset rather than embedding uncontrolled files in an account record. Mail workflows should be drafts until a separately authorized send action exists. Alerts, add-ons, labels, app configuration, and browser-security preferences should each have explicit reviewable fields and an audit trail.

Do not put secrets in query strings, browser storage, audit metadata, UI notifications, error messages, or client-side analytics. Keep login and other Google interactions behind the approved isolated-session boundary, with human/verification attention states represented explicitly rather than bypassed.

## Remaining production checklist

1. Replace the demo actor with production authentication and authorization checks for organization and group ownership on every endpoint.
2. Test duplicate identity, full destination group, stale account version, repeated archive, and concurrent move requests.
3. Test that secrets are absent from responses, logs, audit events, and client storage.
4. Test keyboard navigation, destructive confirmation, loading/error states, and reload-after-success in the frontend.
5. Keep the Phase 1 visual hierarchy: dark navigation rail, group context, readiness boundary, semantic account table, and operation cards inside account detail.
