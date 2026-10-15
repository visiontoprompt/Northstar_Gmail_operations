#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT=/opt/northstar
DATA_ROOT=/var/lib/northstar
ENV_DIR=/etc/northstar
ENV_FILE="$ENV_DIR/northstar.env"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || fail "Run this installer as root: sudo bash deployment/install-ubuntu.sh"
[[ -f "$APP_ROOT/backend/pyproject.toml" && -f "$APP_ROOT/frontend/package-lock.json" ]] || fail "Run this script from the project checkout; backend and frontend sources were not found."
[[ "$(dpkg --print-architecture)" == amd64 ]] || fail "This installer currently supports Ubuntu amd64 servers."
. /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || fail "Use a fresh Ubuntu 24.04 LTS server; other releases are not covered by this installer yet."

printf '\nNorthstar single-server Ubuntu installer\n'
printf 'This installs the SaaS, PostgreSQL, Redis, Chrome, and an admin-protected in-app browser desktop.\n'
read -r -p 'Public hostname (for example app.example.com): ' DOMAIN
read -r -p 'Administrator email (also used for TLS renewal notices): ' ADMIN_EMAIL
read -r -p 'Administrator full name: ' ADMIN_NAME
DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | xargs)"
ADMIN_EMAIL="$(printf '%s' "$ADMIN_EMAIL" | tr '[:upper:]' '[:lower:]' | xargs)"
[[ "$DOMAIN" =~ ^[a-z0-9.-]+\.[a-z]{2,}$ ]] || fail "Enter a valid public DNS hostname."
[[ "$ADMIN_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || fail "Enter a valid administrator email address."
[[ -n "${ADMIN_NAME// /}" ]] || fail "Administrator name cannot be empty."

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg rsync build-essential python3 python3-venv python3-dev \
  postgresql postgresql-contrib redis-server nginx certbot \
  xvfb openbox x11vnc novnc python3-websockify dbus-x11 fonts-liberation fonts-noto-color-emoji \
  libnss3 libatk-bridge2.0-0 libgtk-3-0 libgbm1 libasound2t64 libxkbcommon0 ufw

# Ubuntu 24.04's default Node.js package is older than this frontend's test
# toolchain. Install the supported Node.js 24 LTS line from NodeSource instead.
curl -fsSLo /tmp/nodesource_setup.sh https://deb.nodesource.com/setup_24.x
bash /tmp/nodesource_setup.sh
rm -f /tmp/nodesource_setup.sh
apt-get install -y nodejs
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" == 24 ]] || fail "Node.js 24 is required; found $(node --version)."
printf 'Using Node.js %s and npm %s.\n' "$(node --version)" "$(npm --version)"

curl -fsSLo /tmp/northstar-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt-get install -y /tmp/northstar-chrome.deb
rm -f /tmp/northstar-chrome.deb
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

getent group northstar >/dev/null || groupadd --system northstar
id -u northstar >/dev/null 2>&1 || useradd --system --gid northstar --home-dir "$DATA_ROOT" --shell /usr/sbin/nologin northstar
install -d -o northstar -g northstar -m 0750 "$DATA_ROOT" "$DATA_ROOT/browser-profiles" "$DATA_ROOT/contact-files"
install -d -o root -g northstar -m 0750 "$ENV_DIR"

BOOTSTRAP_NEEDED=false
if [[ ! -f "$ENV_FILE" ]]; then
  BOOTSTRAP_NEEDED=true
  DB_PASSWORD="$(openssl rand -hex 32)"
  SECRET_KEY="$(openssl rand -hex 48)"
  ADMIN_API_TOKEN="$(openssl rand -hex 40)"
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='northstar'" | grep -q 1; then
    runuser -u postgres -- createuser --login northstar
  fi
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 <<SQL
ALTER ROLE northstar WITH LOGIN PASSWORD '${DB_PASSWORD}';
SQL
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='northstar'" | grep -q 1; then
    runuser -u postgres -- createdb --owner=northstar northstar
  fi
  cat > "$ENV_FILE" <<ENV
ENVIRONMENT=production
AUTH_REQUIRED=true
ALLOW_INITIAL_ADMIN_SETUP=false
DATABASE_URL=postgresql+psycopg://northstar:${DB_PASSWORD}@127.0.0.1:5432/northstar
REDIS_URL=redis://127.0.0.1:6379/0
SECRET_KEY=${SECRET_KEY}
ADMIN_API_TOKEN=${ADMIN_API_TOKEN}
BOOTSTRAP_ADMIN_EMAIL=${ADMIN_EMAIL}
CORS_ORIGINS=https://${DOMAIN}
PROFILE_ROOT=${DATA_ROOT}/browser-profiles
BROWSER_EXECUTABLE=/usr/bin/google-chrome-stable
BROWSER_LAUNCH_ENABLED=true
BROWSER_OBSERVER_ENABLED=true
BROWSER_DESKTOP_ENABLED=true
BROWSER_AUTO_LOGIN=true
WORKER_ENABLED=true
WORKER_MAX_ACTIVE_PROFILES=10
ENV
  chmod 0640 "$ENV_FILE"
  chown root:northstar "$ENV_FILE"
else
  cp -a "$ENV_FILE" "${ENV_FILE}.backup.$(date +%Y%m%d%H%M%S)"
  if grep -q '^BROWSER_DESKTOP_ENABLED=' "$ENV_FILE"; then sed -i 's/^BROWSER_DESKTOP_ENABLED=.*/BROWSER_DESKTOP_ENABLED=true/' "$ENV_FILE"; else printf '\nBROWSER_DESKTOP_ENABLED=true\n' >> "$ENV_FILE"; fi
  if grep -q '^BROWSER_LAUNCH_ENABLED=' "$ENV_FILE"; then sed -i 's/^BROWSER_LAUNCH_ENABLED=.*/BROWSER_LAUNCH_ENABLED=true/' "$ENV_FILE"; else printf 'BROWSER_LAUNCH_ENABLED=true\n' >> "$ENV_FILE"; fi
  if grep -q '^BROWSER_OBSERVER_ENABLED=' "$ENV_FILE"; then sed -i 's/^BROWSER_OBSERVER_ENABLED=.*/BROWSER_OBSERVER_ENABLED=true/' "$ENV_FILE"; else printf 'BROWSER_OBSERVER_ENABLED=true\n' >> "$ENV_FILE"; fi
  if grep -q '^BROWSER_EXECUTABLE=' "$ENV_FILE"; then sed -i 's|^BROWSER_EXECUTABLE=.*|BROWSER_EXECUTABLE=/usr/bin/google-chrome-stable|' "$ENV_FILE"; else printf 'BROWSER_EXECUTABLE=/usr/bin/google-chrome-stable\n' >> "$ENV_FILE"; fi
  if grep -q '^WORKER_MAX_ACTIVE_PROFILES=' "$ENV_FILE"; then sed -i 's/^WORKER_MAX_ACTIVE_PROFILES=.*/WORKER_MAX_ACTIVE_PROFILES=10/' "$ENV_FILE"; else printf 'WORKER_MAX_ACTIVE_PROFILES=10\n' >> "$ENV_FILE"; fi
  chown root:northstar "$ENV_FILE"; chmod 0640 "$ENV_FILE"
fi

# Copy this checkout, excluding local environments, secrets, and build caches.
install -d -o root -g root -m 0755 "$INSTALL_ROOT"
if [[ "$APP_ROOT" != "$INSTALL_ROOT" ]]; then
  rsync -a --delete \
    --exclude='.git/' --exclude='.venv/' --exclude='node_modules/' --exclude='dist/' \
    --exclude='__pycache__/' --exclude='*.py[cod]' --exclude='backend/.env' \
    --exclude='frontend/.env*' --exclude='backend/data/' --exclude='sources/' \
    "$APP_ROOT/" "$INSTALL_ROOT/"
fi
chown -R root:northstar "$INSTALL_ROOT"
find "$INSTALL_ROOT" -type d -exec chmod 0755 {} +
find "$INSTALL_ROOT" -type f -exec chmod 0644 {} +
chmod 0750 "$INSTALL_ROOT/deployment/install-ubuntu.sh"

cd "$INSTALL_ROOT/backend"
/usr/local/bin/uv sync --no-dev
cd "$INSTALL_ROOT/frontend"
npm ci
npm run build
chown -R root:www-data "$INSTALL_ROOT/frontend/dist"
find "$INSTALL_ROOT/frontend/dist" -type d -exec chmod 0755 {} +
find "$INSTALL_ROOT/frontend/dist" -type f -exec chmod 0644 {} +

install -o root -g root -m 0644 "$INSTALL_ROOT/deployment/northstar.service" /etc/systemd/system/northstar.service
install -o root -g root -m 0644 "$INSTALL_ROOT/deployment/northstar-xvfb.service" /etc/systemd/system/northstar-xvfb.service
install -o root -g root -m 0644 "$INSTALL_ROOT/deployment/northstar-openbox.service" /etc/systemd/system/northstar-openbox.service
install -o root -g root -m 0644 "$INSTALL_ROOT/deployment/northstar-x11vnc.service" /etc/systemd/system/northstar-x11vnc.service
install -o root -g root -m 0644 "$INSTALL_ROOT/deployment/northstar-websockify.service" /etc/systemd/system/northstar-websockify.service

# Certbot uses a temporary HTTP-only vhost so the certificate is ready before
# the final HTTPS vhost references it.
install -d -o www-data -g www-data -m 0755 /var/www/northstar-certbot
cat > /etc/nginx/sites-available/northstar-certbot <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};
    root /var/www/northstar-certbot;
    location /.well-known/acme-challenge/ { try_files \$uri =404; }
}
NGINX
ln -sfn /etc/nginx/sites-available/northstar-certbot /etc/nginx/sites-enabled/northstar
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx
certbot certonly --webroot -w /var/www/northstar-certbot -d "$DOMAIN" --non-interactive --agree-tos -m "$ADMIN_EMAIL"
sed "s/app.example.com/${DOMAIN}/g" "$INSTALL_ROOT/deployment/nginx.conf.template" > /etc/nginx/sites-available/northstar
ln -sfn /etc/nginx/sites-available/northstar /etc/nginx/sites-enabled/northstar
nginx -t

systemctl enable postgresql redis-server
systemctl enable certbot.timer
systemctl restart postgresql redis-server
systemctl daemon-reload
systemctl enable --now northstar-xvfb northstar-openbox northstar-x11vnc northstar-websockify

# Provision the database before creating the first administrator.
runuser -u northstar -- sh -c 'set -a; . /etc/northstar/northstar.env; set +a; cd /opt/northstar/backend && .venv/bin/alembic -c alembic.ini upgrade head'
HAS_ADMIN="$(runuser -u northstar -- sh -c 'set -a; . /etc/northstar/northstar.env; set +a; cd /opt/northstar/backend && .venv/bin/python -c "from sqlalchemy import select; from app.db import SessionLocal; from app.models import EmployeeAuth; db=SessionLocal(); print(\"yes\" if db.scalar(select(EmployeeAuth.id).limit(1)) else \"no\"); db.close()"')"
if [[ "$BOOTSTRAP_NEEDED" == true || "$HAS_ADMIN" != yes ]]; then
  printf '\nCreate the first SaaS administrator now. Password input is hidden.\n'
  runuser -u northstar -- sh -c 'set -a; . /etc/northstar/northstar.env; set +a; cd /opt/northstar/backend && .venv/bin/python -m app.bootstrap_admin' || {
    printf 'Administrator provisioning did not complete. Rerun the installer after correcting the issue; it will keep the saved environment and database.\n' >&2
    exit 1
  }
fi
if [[ "$BOOTSTRAP_NEEDED" == true ]]; then
  runuser -u northstar -- sh -c 'set -a; . /etc/northstar/northstar.env; set +a; cd /opt/northstar/backend && .venv/bin/python -c "from sqlalchemy import select; from app.config import get_settings; from app.db import SessionLocal; from app.models import WorkspaceSettings; from app.service import get_workspace_settings; cfg=get_settings(); db=SessionLocal(); get_workspace_settings(db, cfg.demo_org_id); row=db.scalar(select(WorkspaceSettings).where(WorkspaceSettings.organization_id==cfg.demo_org_id)); row.default_login_concurrency=5; row.max_concurrent_operations=10; row.max_active_profiles=10; db.commit(); db.close()"'
fi

systemctl enable --now northstar
systemctl reload nginx
SSH_PORT="$(sshd -T 2>/dev/null | awk '$1 == "port" { print $2; exit }')"
if [[ "$SSH_PORT" =~ ^[0-9]+$ ]]; then ufw allow "${SSH_PORT}/tcp"; else ufw allow OpenSSH || ufw allow 22/tcp; fi
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

printf '\nNorthstar is installed.\n'
printf 'Open: https://%s\n' "$DOMAIN"
printf 'Browser desktop: sign in as administrator, then open Browser desktop in the sidebar.\n'
printf 'Service status: systemctl status northstar\n'
printf 'Logs: journalctl -u northstar -f\n'
printf 'Keep /etc/northstar/northstar.env and %s backed up and restricted.\n' "$DATA_ROOT"
