# Gmail Operations SaaS

Phase 1 is the account registry and onboarding boundary for a company-managed Gmail operations platform.

This phase can:

- create and manage account groups sized for roughly 300 accounts;
- add one account or preview/import many accounts using `email:password:recovery:2fa`;
- validate and deduplicate accounts inside an organization;
- keep credential material in an encrypted secrets table, separate from operational metadata;
- reserve dedicated proxy and persistent browser-profile metadata for later provisioning;
- open a durable, profile-backed human login hand-off and record its lifecycle;
- queue a whole group for concurrency-controlled isolated profile dispatch;
- version encrypted credential updates and mark accounts for re-authentication;
- expose searchable, filterable admin account views and an audit trail.

The complete product direction and delivery sequence are defined in [ROADMAP.md](ROADMAP.md). The system topology, domain ownership, state model, and module boundaries are defined in [docs/architecture.md](docs/architecture.md).

The current session hand-off deliberately does not solve or bypass Gmail CAPTCHA/MFA/security challenges, use browser stealth, or use Gmail API/OAuth. It persists profile reservations, credential revisions, and concurrency-controlled session queues so a reviewed browser worker can attach safely. Imported accounts end in `READY_FOR_LOGIN`, which is the hand-off boundary for login.

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

Session records contain only account/profile references, lifecycle state, timestamps, and controlled runtime metadata. They never contain imported credential material. See [docs/phase-2-session.md](docs/phase-2-session.md) for the hand-off contract.

Login page handling is observation-first: normal Google interstitials can be classified, while CAPTCHA, MFA, and security checks pause for human action. See [docs/login-safety.md](docs/login-safety.md).

## Verification

```bash
cd backend && pytest
cd frontend && npm run build
```

The Phase 1 API is documented at `http://localhost:8000/docs` while the backend is running. See [docs/phase-1.md](docs/phase-1.md) for the endpoint contract and future hand-off notes.
