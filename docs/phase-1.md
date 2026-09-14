# Phase 1 API and hand-off

The API is intentionally scoped to account onboarding. It creates operational records and encrypted credential references but never attempts a Gmail login.

## Endpoints

- `GET /api/v1/health` — service health.
- `GET /api/v1/groups` — organization groups and capacity counts.
- `POST /api/v1/groups` — create a group with a default capacity of 300.
- `GET /api/v1/accounts` — filter/search accounts with `q`, `group_id`, `status`, `page`, and `page_size`.
- `GET /api/v1/accounts/{id}` — safe account detail shell with metadata, proxy/profile placeholders, and recent audit events.
- `POST /api/v1/accounts/single` — add one account with structured fields.
- `POST /api/v1/accounts/bulk/preview` — validate import lines without persistence.
- `POST /api/v1/accounts/bulk/import` — validate, encrypt, persist, and audit rows.
- `GET /api/v1/audit-events` — paginated operational audit events.

## Phase 2 contract

Phase 2 can consume accounts whose `readiness_status` is `READY_FOR_LOGIN`. It should create a provisioning job, allocate the dedicated proxy/profile placeholders, and transition only from concrete browser/Gmail events. It must not infer `HEALTHY` from import success.

The future worker path remains:

```text
API → Redis queue/lock → stateless Playwright worker → durable profile + dedicated proxy
```

No worker, browser, queue, lock, or Gmail automation is included in Phase 1.
