# Session hand-off boundary

This increment adds the durable boundary between account onboarding and Gmail login.

## What is stored

- `browser_profiles` reserves one persistent profile record per Gmail account. It stores only the profile identifier, storage reference, lifecycle status, and lock state.
- `browser_sessions` records each requested login session, its profile, runtime boundary, state, and timestamps.
- `audit_events` records session start, login-state events, and close events with account/profile IDs only.

The account secret remains in the encrypted secret store. Session responses, audit metadata, and the frontend never contain password, recovery, or 2FA values.

## Login hand-off flow

1. Admin opens account detail and selects **Open login session**.
2. The API locks the account’s profile and returns a `LOGIN_REQUIRED` session with a local session workspace URL.
3. When `BROWSER_LAUNCH_ENABLED=true` in local development, the API opens a separate Chrome process using `--user-data-dir` for that profile. The SaaS console remains in its original browser session. A future Playwright worker will own this runtime boundary in production.
4. A trusted browser/runtime event can record `LOGIN_COMPLETED` or `VERIFICATION_REQUIRED`.
5. Closing the session releases the profile lock but preserves its storage reference and session history.

The application does not automate credential entry, bypass CAPTCHA/MFA, or use stealth/fingerprint manipulation. Google authentication remains human-controlled until the worker contract is implemented and reviewed. If the local launcher is disabled or unavailable, the session record remains valid as a worker hand-off without pretending that a browser opened.

## API

- `POST /api/v1/accounts/{id}/sessions` — reserve the persistent profile and open a login session.
- `GET /api/v1/sessions/{id}` — read the safe session state.
- `GET /api/v1/sessions?account_id={id}` — list session history.
- `POST /api/v1/sessions/{id}/events` — record `LOGIN_COMPLETED`, `VERIFICATION_REQUIRED`, or `HEARTBEAT`.
- `POST /api/v1/sessions/{id}/close` — close the session and release its profile lock.
