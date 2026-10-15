# Single-server Ubuntu deployment

`install-ubuntu.sh` installs Northstar and its runtime on one fresh Ubuntu amd64 server. It installs PostgreSQL, Redis, Python/uv, Node, Chrome, Xvfb, Openbox, x11vnc, noVNC/Websockify, Nginx, and Certbot; builds the UI; applies database migrations; provisions the initial administrator; enables HTTPS; and starts the application services.

## Before running it

1. Prepare a fresh Ubuntu 24.04 LTS amd64 server. For 5 visible browser sessions, start around 8 vCPU / 16 GB RAM; for 10, start around 12–16 vCPU / 32 GB RAM. Chromium/Gmail memory use varies, so watch RAM and swap during a real workload before increasing concurrency.
2. Point a public DNS A record for the chosen hostname at the server and allow inbound ports 22, 80, and 443. The hostname must resolve publicly before the installer requests its TLS certificate.
3. Copy the complete project checkout to the server. The script installs from that checkout; it does not fetch an unspecified Git repository or create DNS records.
4. SSH into the server and run:

   ```bash
   cd /path/to/project
   sudo bash deployment/install-ubuntu.sh
   ```

The installer asks for the public hostname, administrator email, and name. It generates random database, encryption, and break-glass API secrets and stores them in `/etc/northstar/northstar.env` with restricted permissions. It prompts for the initial administrator password without echoing it. Keep the environment file and `/var/lib/northstar` backed up and access-restricted.

## Browser desktop and automation

The installer enables the existing Playwright persistent-profile automation and places normal headed Chrome windows on one virtual 1600×900 X display. The SaaS adds a **Browser desktop** page that embeds noVNC, so the administrator can inspect and control the same server-side browser windows from the app. Closing the viewer does not close account sessions. The VNC and WebSocket listeners bind to loopback only; Nginx protects both desktop assets and the control socket with an application-authenticated, administrator-only check. Ports 5900 and 6080 are not opened in the firewall.

All browser windows share one desktop, so multiple concurrent sessions may overlap. Use Alt+Tab in the viewer to switch between windows. The application worker cap is set to 10; the account UI's configured concurrency remains the operational limit. Session state and profile locks continue to be managed by the existing app.

## Services and checks

```bash
systemctl status northstar northstar-xvfb northstar-openbox northstar-x11vnc northstar-websockify
journalctl -u northstar -f
sudo nginx -t
curl -fsS https://YOUR_HOSTNAME/api/v1/health
```

PostgreSQL and Redis listen locally. Uvicorn listens on `127.0.0.1:8000`; Nginx serves the production frontend and proxies authenticated app/API traffic over HTTPS. The installer keeps Uvicorn at one worker because current observers and dispatch state are process-local. It enables UFW for SSH/HTTP/HTTPS only and enables Certbot renewal.

To update the application, copy the updated project checkout to the server and rerun the installer. Existing environment values are backed up and retained; the existing database and administrator are not reset. The deployment uses an initial baseline Alembic migration for a fresh database. For a database already containing production data, take and test a backup and compare its schema before using this deployment flow.
