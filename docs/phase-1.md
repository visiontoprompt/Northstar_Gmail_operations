# Phase 1 API and hand-off

The API is intentionally scoped to account onboarding plus the controlled hand-off into the login queue. It creates operational records and encrypted credential references; it does not solve Google security challenges.

## Endpoints

- `GET /api/v1/health` — service health.
- `GET /api/v1/groups` — organization groups and capacity counts.
- `POST /api/v1/groups` — create a group with a default capacity of 300.
- `PATCH /api/v1/groups/{id}` — edit group name, description, capacity, or active/paused state.
- `GET /api/v1/employees` — search the organization employee directory by name, email, role, or status.
- `POST /api/v1/employees` — create an invited employee record.
- `PATCH /api/v1/employees/{id}` — edit employee identity, role, notes, or access status.
- `DELETE /api/v1/employees/{id}` — soft-deactivate an employee while preserving the audit record.
- `GET /api/v1/employees/{id}/group-access` — read the employee's current group assignments and access levels.
- `PUT /api/v1/employees/{id}/group-access` — replace assignments atomically with `VIEWER`, `OPERATOR`, or `MANAGER` levels; the mutation is audited.
- `GET /api/v1/auth/status`, `POST /api/v1/auth/setup`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, and `POST /api/v1/auth/logout` — manage the local employee workspace session using an HttpOnly cookie.
- `PUT /api/v1/employees/{id}/password` — administrator-only password provisioning; only a one-way scrypt hash is stored.
- `GET /api/v1/settings` and `PUT /api/v1/settings` — read and persist workspace policy controls; deployment secrets and process flags are read-only/out of scope.
- `GET /api/v1/accounts` — filter/search accounts with `q`, `group_id`, `status`, `page`, and `page_size`.
- `GET /api/v1/accounts/{id}` — safe account detail shell with metadata, proxy/profile placeholders, and recent audit events.
- `POST /api/v1/accounts/single` — add one account with structured fields.
- `POST /api/v1/accounts/bulk/preview` — validate import lines without persistence.
- `POST /api/v1/accounts/bulk/import` — validate, encrypt, persist, and audit rows.
- `GET /api/v1/audit-events` — paginated operational audit events.
- `PUT /api/v1/accounts/{id}/credentials` — create a new encrypted credential revision and require re-authentication.
- `GET /api/v1/accounts/{id}/credential-revisions` — return masked revision metadata.
- `POST /api/v1/login-jobs` — create a group-level login queue with explicit concurrency.
- `GET /api/v1/login-jobs` and `GET /api/v1/login-jobs/{id}` — inspect queues and per-account progress.
- `POST /api/v1/login-jobs/{id}/start`, `/dispatch`, `/pause`, and `/cancel` — control isolated profile dispatch.

## Phase 2 contract

Phase 2 can consume accounts whose `readiness_status` is `READY_FOR_LOGIN`. It should create a provisioning job, allocate the dedicated proxy/profile placeholders, and transition only from concrete browser/Gmail events. It must not infer `HEALTHY` from import success.

The future worker path remains:

```text
API → Redis queue/lock → stateless Playwright worker → durable profile + dedicated proxy
```

The session and batch hand-off boundary is documented in [phase-2-session.md](phase-2-session.md). It reserves durable profiles and opens concurrency-limited isolated sessions; CAPTCHA/MFA/security challenges remain paused for operator attention.
