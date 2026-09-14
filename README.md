# Gmail Operations SaaS

Phase 1 is the account registry and onboarding boundary for a company-managed Gmail operations platform.

This phase can:

- create and manage account groups sized for roughly 300 accounts;
- add one account or preview/import many accounts using `email:password:recovery:2fa`;
- validate and deduplicate accounts inside an organization;
- keep credential material in an encrypted secrets table, separate from operational metadata;
- reserve dedicated proxy and persistent browser-profile metadata for later provisioning;
- expose searchable, filterable admin account views and an audit trail.

This phase deliberately does not automate Gmail login, browser control, CAPTCHA/MFA handling, or profile provisioning. Imported accounts end in `READY_FOR_LOGIN`, which is the hand-off boundary for Phase 2.

## Repository layout

```text
backend/       FastAPI service, SQLAlchemy models, migrations, tests
frontend/      React + Vite admin application
docs/          Phase notes and API boundary documentation
```

## Local development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export SECRET_KEY='replace-with-a-32-byte-base64-fernet-key'
uvicorn app.main:app --reload --port 8000
```

The development default uses SQLite at `./data/gmail_ops.db`. Set `DATABASE_URL` to PostgreSQL for a shared environment. The API creates the demo organization and starter groups on first boot in development.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite proxy sends `/api` requests to `http://localhost:8000`.

## Security boundary

The raw import line is accepted only at the API boundary, parsed in memory, encrypted before persistence, and never returned, logged, placed in an audit event, or written to browser storage. The UI only shows masked recovery metadata and a `Configured` marker for 2FA. Production deployments must provide a managed `SECRET_KEY`/KMS-backed key and PostgreSQL; the development key fallback is not suitable for production.

## Verification

```bash
cd backend && pytest
cd frontend && npm run build
```

The Phase 1 API is documented at `http://localhost:8000/docs` while the backend is running. See [docs/phase-1.md](docs/phase-1.md) for the endpoint contract and future hand-off notes.
