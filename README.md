# Gmail Operations SaaS

Phase 1 is the account registry and onboarding boundary for a company-managed Gmail operations platform.

This phase can:

- create and manage account groups sized for roughly 300 accounts;
- add one account or preview/import many accounts using `email:password:recovery:2fa`;
- validate and deduplicate accounts inside an organization;
- keep credential material in an encrypted secrets table, separate from operational metadata;
- reserve dedicated proxy metadata and persistent browser-profile metadata for profile-backed operations;
- open a durable, profile-backed login session and record its lifecycle;
- queue a whole group for concurrency-controlled isolated profile dispatch;
- version encrypted credential updates and mark accounts for re-authentication;
- expose searchable, filterable admin account views and an audit trail.
- keep reusable CSV/VCF/TXT contact files in an organization library and launch profile-backed imports at Google Contacts.
- open Gmail's Add-ons rail and install up to three randomly selected, allowlisted Workspace Marketplace listings per authenticated account, with a configurable bulk concurrency limit.

The complete product direction and delivery sequence are defined in [ROADMAP.md](ROADMAP.md). The system topology, domain ownership, state model, and module boundaries are defined in [docs/architecture.md](docs/architecture.md).

The local session observer may enter credentials already stored in the encrypted
vault only on Google's own sign-in host. It does not solve or bypass CAPTCHA,
MFA, or security challenges, use Gmail API/OAuth, or claim success from a login
form alone. A profile is marked saved only after a Gmail mailbox is observed.
Definitive account-not-found results are recorded as `NONEXISTENT`, the browser
session is closed, and the unsaved profile directory is removed. Imported
accounts still end in `READY_FOR_LOGIN`, which is the hand-off boundary for
login.

## Repository layout

```text
backend/       FastAPI service, SQLAlchemy models, migrations, tests
frontend/      React + Vite admin application
docs/          Phase notes and API boundary documentation
```

## Local development

### Start both services with one command

From the repository root:

```bash
./dev.sh
```

This starts the backend on port 8000 and the frontend on port 5173. Keep the terminal open; press Ctrl+C to stop both services together.

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export SECRET_KEY='replace-with-a-32-byte-base64-fernet-key'
uvicorn app.main:app --reload --port 8000
```

Copy `backend/.env.example` when setting up a local environment. In production, use a managed secret provider and replace the development SQLite default with PostgreSQL.

For local browser-session testing only, set `BROWSER_LAUNCH_ENABLED=true`. Each account then opens in its own Chrome process and persistent user-data directory, separate from the browser window hosting the SaaS console. The app never passes imported passwords, recovery values, or 2FA secrets to Chrome.

The development default uses SQLite at `./data/gmail_ops.db`. Set `DATABASE_URL` to PostgreSQL for a shared environment. The API creates the demo organization and starter groups on first boot in development.

Outside development, configure `ADMIN_API_TOKEN` and send it as `Authorization: Bearer <token>` on API requests. The local development bypass is intentional for the demo console only; production deployments must replace this bootstrap token with the organization's real identity provider and role-based authorization before exposing the service.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite proxy sends `/api` requests to `http://localhost:8000`.

## Security boundary

The raw import line is accepted only at the API boundary, parsed in memory, encrypted before persistence, and never returned, logged, placed in an audit event, or written to browser storage. The UI only shows masked recovery metadata and a `Configured` marker for 2FA. Production deployments must provide a managed `SECRET_KEY`/KMS-backed key and PostgreSQL; the development key fallback is not suitable for production.

Contact import files are stored separately from account records under the configured profile root. The registry Contacts action asks the operator to select a ready library file, then starts the import only for an authenticated profile. File contents are not returned by the API or written to audit metadata.

Session records contain only account/profile references, lifecycle state, timestamps, and controlled runtime metadata. They never contain imported credential material. See [docs/phase-2-session.md](docs/phase-2-session.md) for the hand-off contract.

The Sessions page defaults to one latest action per account and can switch to All activity. It filters by group, action (login, reopen profile, contacts, Labels & Markers, or Gmail add-ons), and evidence-derived outcome: in progress, completed, interrupted, not finished, or failed. The outcome is derived from persisted session events and close state, not guessed by the frontend.

Account proxies can be assigned during single-account intake or from account metadata editing. Supported values are `ip:port` and `username:password:ip:port`; the connection is encrypted with the account vault and supplied only to that account's isolated Playwright browser at launch. Proxy usernames and passwords are never returned in API responses or logs.

The Proxies inventory is account-scoped and group-filterable: it groups identical assigned endpoints within the selected group scope, lets an administrator test up to 300 selected endpoints, and records safe public-IP, provider, country, city, and latency observations from the configured health-check URL. It also supports reassignment, metadata edits, unassignment, and confirmed removal. A provider or location is shown only after a successful health check; the app never fabricates those values.

Login page handling is observation-first: normal Google interstitials can be classified, account-not-found copy is recognized in English and French, and CAPTCHA/MFA/security checks pause for human action. See [docs/login-safety.md](docs/login-safety.md).

Gmail add-ons use a curated set of official Workspace Marketplace listing URLs. The observer searches the Marketplace and verifies up to three installs per account. Google or a Marketplace publisher may still require permission, OAuth, billing, or subscription approval; those prompts are recorded as `NEEDS_ATTENTION` and are never accepted automatically.

Gmail add-ons use a curated set of official Workspace Marketplace listing URLs. The observer searches the Marketplace and verifies up to three installs per account. Google or a Marketplace publisher may still require permission, OAuth, billing, or subscription approval; those prompts are recorded as `NEEDS_ATTENTION` and are never accepted automatically.

## Verification

```bash
cd backend && pytest
cd frontend && npm run build
```

The Phase 1 API is documented at `http://localhost:8000/docs` while the backend is running. See [docs/phase-1.md](docs/phase-1.md) for the endpoint contract and future hand-off notes.
