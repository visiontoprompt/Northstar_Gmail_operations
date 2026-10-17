# Gmail Operations SaaS

Phase 1 is the account registry and onboarding boundary for a company-managed Gmail operations platform.

This phase can:

- create and manage account groups sized for roughly 300 accounts;
- add one account or preview/import many accounts using `email:password`, `email:password:recovery`, or `email:password:recovery:2fa`;
- validate and deduplicate accounts inside an organization;
- keep credential material in an encrypted secrets table, separate from operational metadata;
- reserve dedicated proxy metadata and persistent browser-profile metadata for profile-backed operations;
- open a durable, profile-backed login session and record its lifecycle;
- queue a whole group for concurrency-controlled isolated profile dispatch;
- version encrypted credential updates and mark accounts for re-authentication;
- expose searchable, filterable admin account views and an audit trail.
- manage account groups, organization employees, and persisted workspace policy settings from dedicated admin pages.
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

This applies pending Alembic migrations to the configured local database, then starts the backend on port 8000 and the frontend on port 5173. Keep the terminal open; press Ctrl+C to stop both services together. If a backend is already running on port 8000, stop it before restarting `./dev.sh` so the migration runs before startup recovery.

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

For Ubuntu production hosting, use the systemd, Nginx, environment, and migration templates in [deployment/README.md](deployment/README.md). Production startup refuses SQLite, requires authentication, and verifies that the Alembic schema revision is current. Apply schema changes with `cd backend && alembic -c alembic.ini upgrade head`; new schema changes must be added as Alembic revisions. The legacy SQL files under `backend/migrations/` document earlier schema history.

## Install on Ubuntu

The single-server installer supports a **fresh Ubuntu 24.04 LTS amd64** server. Before starting, point a public DNS A record for your app hostname at the server and allow inbound SSH, HTTP, and HTTPS (ports 22, 80, and 443). For about five concurrent visible browser sessions, plan for at least 8 vCPU and 16 GB RAM; for up to ten, plan for 12–16 vCPU and 32 GB RAM.

Because this GitHub repository is private, give the server or the operator account a GitHub deploy key with read access, or copy the checkout to the server using your approved secure transfer method. Then run:

```bash
git clone git@github.com:visiontoprompt/Northstar_Gmail_operations.git
cd Northstar_Gmail_operations
sudo bash deployment/install-ubuntu.sh
```

The installer checks the Ubuntu version and architecture, then asks for the public hostname, administrator email, and administrator name. It installs PostgreSQL, Redis, Python/uv, Node.js 24 LTS, Chrome, the virtual browser desktop, Nginx, and Certbot; builds the frontend; applies database migrations; and provisions the first administrator with a hidden password prompt. It generates the database password, application encryption key, and break-glass API token and saves them in `/etc/northstar/northstar.env` with restricted permissions. The installer also obtains the TLS certificate and enables the required system services and firewall rules.

After installation, open `https://YOUR_HOSTNAME` and sign in with the administrator account you created. Check the services and application health with:

```bash
sudo systemctl status northstar northstar-xvfb northstar-openbox northstar-x11vnc northstar-websockify
sudo journalctl -u northstar -f
sudo nginx -t
curl -fsS https://YOUR_HOSTNAME/api/v1/health
```

To update an existing installation, update the project checkout and rerun `sudo bash deployment/install-ubuntu.sh` from its root. The installer backs up and retains the existing environment and database. Keep `/etc/northstar/northstar.env` and `/var/lib/northstar` access-restricted and backed up. For sizing, browser-desktop behavior, and migration caveats, see [deployment/README.md](deployment/README.md).

For local browser-session testing only, set `BROWSER_LAUNCH_ENABLED=true`. Each account then opens in its own Chrome process and persistent user-data directory, separate from the browser window hosting the SaaS console. The app never passes imported passwords, recovery values, or 2FA secrets to Chrome.

The development default uses SQLite at `./data/gmail_ops.db`. Set `DATABASE_URL` to PostgreSQL for a shared environment. The API creates the demo organization and starter groups on first boot in development.

Employee workspace sign-in is enabled by `./dev.sh`. On a fresh database, the console shows a one-time administrator setup screen. The administrator can create employees, set or rotate their workspace sign-in password, and assign each employee explicit group access. Operators and auditors receive only their assigned groups; administrators retain full workspace control. Employee passwords are stored as scrypt hashes and sessions use an opaque HttpOnly cookie. Gmail account passwords, recovery addresses, and 2FA values remain in the existing encrypted account vault and are never reused as employee login credentials.

For legacy local inspection, `AUTH_REQUIRED=false` keeps the development-only demo administrator fallback. Do not use that fallback for a shared or production deployment. Disable `ALLOW_INITIAL_ADMIN_SETUP` outside development and review the role/group policy before exposing the service.

The Groups page supports audited name, description, capacity, and active/paused
state changes; capacity cannot be lowered below the active accounts already in
the group. The Employees page supports organization-scoped create, edit, role
changes, suspend/reactivate, and soft deactivation. The Settings page persists
workspace policy such as default group capacity, login/operation limits,
profile limits, observer cadence, session timeout, automatic session closure,
and audit retention. Employees can also be assigned to one or more groups with
View only, Operate accounts, or Manage group access levels; those assignments
are replaced atomically and audited. Deployment secrets, API tokens, database/Redis URLs, and
process-level browser flags remain outside the UI.

Outside development, configure `ADMIN_API_TOKEN` for break-glass API administration and use employee cookie sessions for normal workspace access. Production deployments must use PostgreSQL, place the service behind HTTPS, disable initial setup, and use a managed identity/MFA policy before exposing it to operators. Login endpoints also enforce application-level throttling and account lockout; Nginx adds an IP-level limit in the supplied template.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite proxy sends `/api` requests to `http://localhost:8000`. Frontend dependency audits also run from this directory with `npm audit`; the npm lockfile is `frontend/package-lock.json`, and there is no root-level npm lockfile.

## Security boundary

The raw import line is accepted only at the API boundary, parsed in memory, encrypted before persistence, and never returned, logged, placed in an audit event, or written to browser storage. The UI only shows masked recovery metadata and a `Configured` marker for 2FA. Production deployments must provide a managed `SECRET_KEY`/KMS-backed key and PostgreSQL; the development key fallback is not suitable for production.

Contact import files are stored separately from account records under the configured profile root. The registry Contacts action asks the operator to select a ready library file, then starts the import only for an authenticated profile. File contents are not returned by the API or written to audit metadata.

Session records contain only account/profile references, lifecycle state, timestamps, and controlled runtime metadata. They never contain imported credential material. See [docs/phase-2-session.md](docs/phase-2-session.md) for the hand-off contract.

The Sessions page defaults to one latest action per account and can switch to All activity. It filters by group, action (login, reopen profile, contacts, Labels & Markers, or Gmail add-ons), and evidence-derived outcome: in progress, completed, interrupted, not finished, or failed. The outcome is derived from persisted session events and close state, not guessed by the frontend.

Account proxies can be assigned during single-account intake or from account metadata editing. Supported values are `ip:port` and `username:password:ip:port`; the connection is encrypted with the account vault and supplied only to that account's isolated Playwright browser at launch. Proxy usernames and passwords are never returned in API responses or logs.

The Proxies inventory is account-scoped and group-filterable: it groups identical assigned endpoints within the selected group scope, lets an administrator test up to 300 selected endpoints, and records safe public-IP, provider, country, city, and latency observations from the configured health-check URL. It supports reassignment, metadata edits, unassignment, and confirmed removal. Proxy groups can be created, renamed, and deleted; deleting a group ungroups its endpoints and removes linked OVH group metadata while preserving proxy records, encrypted connections, and account assignments. `Manage endpoints` filters the inventory so selected endpoints can be moved between groups or ungrouped. A provider or location is shown only after a successful health check; the app never fabricates those values.

Login page handling is observation-first: normal Google interstitials can be classified, account-not-found copy is recognized in English and French, and CAPTCHA/MFA/security checks pause for human action. See [docs/login-safety.md](docs/login-safety.md).

Gmail add-ons use a curated set of official Workspace Marketplace listing URLs. The observer searches the Marketplace, approves the localized Google Marketplace installation confirmation, and verifies up to three installs per account. Publisher OAuth scopes, billing, subscription, or other unrelated account-consent prompts are recorded as `NEEDS_ATTENTION` and are never accepted automatically.


## Verification

```bash
cd backend && pytest
cd frontend && npm run build
```

The Phase 1 API is documented at `http://localhost:8000/docs` while the backend is running. See [docs/phase-1.md](docs/phase-1.md) for the endpoint contract and future hand-off notes.
