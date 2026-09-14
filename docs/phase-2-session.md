# Session hand-off boundary

This increment adds the durable boundary between account onboarding and Gmail login.

## What is stored

- `browser_profiles` reserves one persistent profile record per Gmail account. It stores only the profile identifier, storage reference, lifecycle status, and lock state.
- `browser_sessions` records each requested login session, its profile, runtime boundary, state, and timestamps.
- `audit_events` records session start, login-state events, and close events with account/profile IDs only.

The account secret remains in the encrypted secret store. Session responses, audit metadata, and the frontend never contain password, recovery, or 2FA values.

## Login hand-off flow

1. Admin opens account detail and selects **Open login session**, or starts a controlled login batch for a group.
2. The API locks each account profile and returns `LOGIN_REQUIRED` sessions. A batch opens only its configured concurrency and keeps the remaining accounts queued.
3. When `BROWSER_LAUNCH_ENABLED=true` in local development, the API opens a separate Chrome process using `--user-data-dir` for that profile. The SaaS console remains in its original browser session. A future Playwright worker will own this runtime boundary in production.
4. A trusted browser/runtime event can record `LOGIN_COMPLETED` or `VERIFICATION_REQUIRED`; the associated batch item is updated automatically.
5. Closing the session releases the profile lock but preserves its storage reference and session history.

The application does not solve or bypass CAPTCHA/MFA, or use stealth/fingerprint manipulation. Stored credential changes are versioned and encrypted, and mark the account for re-authentication. If the local launcher is disabled or unavailable, the session record remains valid as a worker hand-off without pretending that a browser opened.

## API

- `POST /api/v1/accounts/{id}/sessions` — reserve the persistent profile and open a login session.
- `GET /api/v1/sessions/{id}` — read the safe session state.
- `GET /api/v1/sessions?account_id={id}` — list session history.
- `POST /api/v1/sessions/{id}/events` — record `LOGIN_COMPLETED`, `VERIFICATION_REQUIRED`, or `HEARTBEAT`.
- `POST /api/v1/sessions/{id}/close` — close the session and release its profile lock.
- `PUT /api/v1/accounts/{id}/credentials` — store a new encrypted credential revision and require re-authentication.
- `GET /api/v1/accounts/{id}/credential-revisions` — list masked revision metadata only.
- `POST /api/v1/login-jobs` — create a group/account login queue with a concurrency limit.
- `GET /api/v1/login-jobs/{id}` — read batch progress and per-account states.
- `POST /api/v1/login-jobs/{id}/start` — dispatch the first concurrency-limited wave of isolated profiles.
- `POST /api/v1/login-jobs/{id}/dispatch` — dispatch the next available wave.
- `GET /api/v1/login-jobs/{id}/progress` — read the same progress payload for polling clients.
- `POST /api/v1/login-jobs/{id}/dispatch-next` — dispatch the next available wave using an explicit operation name.
- `POST /api/v1/login-jobs/{id}/resume` — resume a paused batch and dispatch available profiles.
- `POST /api/v1/login-jobs/{id}/pause` / `cancel` — control queued work and release profiles on cancellation.
- `POST /api/v1/maintenance/recover-stale-sessions` — release profiles whose workers stopped reporting heartbeats and mark them for attention.
