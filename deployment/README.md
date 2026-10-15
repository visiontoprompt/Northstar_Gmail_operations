# Single-server Ubuntu deployment

`install-ubuntu.sh` installs Northstar and its runtime on one fresh Ubuntu amd64 server. It installs PostgreSQL, Redis, Python/uv, Node.js 24 LTS, Chrome, Xvfb, Openbox, `wmctrl` for focusing account windows, x11vnc, noVNC/Websockify, Nginx, and Certbot; builds the UI; applies database migrations; provisions the initial administrator; enables HTTPS; and starts the application services. Node.js 24 is required by the frontend's current test toolchain and is checked by the installer before it continues.

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

## OVH US Floating IP recovery

Starting the configured OVH Public Cloud instance now creates a durable recovery job. It waits for the instance to become `ACTIVE`, allows a bounded network/Squid grace period, checks all 15 configured proxy addresses from the application server using proxy authentication and an IPv4 IP-check endpoint, then retries transient failures. It does not rebind healthy proxies, 407 authentication failures, or proxies returning an unexpected exit IP. For persistent connection failures only, the app validates every Floating IP against its expected fixed private IP, saves the Floating IP resource ID and original port/private-IP binding in PostgreSQL, then re-associates affected addresses sequentially. A failed re-association triggers restoration retries using that saved binding. Recovery state is resumed after API service restarts. Stop requests cancel between IPs and wait for an in-flight binding restoration before issuing power-off.

This is an automated workaround for the observed OVH networking behavior, not a confirmed explanation or fix for its cause. The app never allocates or deletes Floating IPs, changes fixed IPs, deletes ports/NICs, or changes the instance's main public IP. Do not change the expected IP-to-private-IP list without validating the network plan.

Configure these values on the server, not in the UI or source repository:

1. In `/etc/northstar/northstar.env`, set `OVH_PROXY_RECOVERY_USERNAME` and `OVH_PROXY_RECOVERY_PASSWORD` to the Squid credentials and retain the configured project, instance, region, and port values. The environment file should remain owned by `root:northstar` and mode `0640`.
2. Create `/etc/northstar/openstack/` with owner `root:northstar`, mode `0750`, then place an OVH-generated `clouds.yaml` there, owned by `root:northstar` and mode `0640`. Use the OpenStack credentials for the US project and set the cloud entry name to `northstar-ovh-us`; its `project_id` must be `239fa25938524e2f87575cc424a2b6fc` and `region_name` must be `US-EAST-VA-1`. Set `OS_CLIENT_CONFIG_FILE` to that file path in the environment file. Do not use OVH application/consumer keys as Keystone credentials.
3. Grant the OpenStack identity only what is needed to list/get existing Floating IPs, read their existing ports, and update the Floating IP `port_id` association (`network:list_floatingips`, `network:get_floatingip`, `network:get_port`, and `network:update_floatingip`, or OVH's equivalent project-level network permissions). The SDK verifies the configured project and region before it can change any association; the app also confirms each port belongs to the configured instance and contains the expected fixed IP.
4. Install/update the app so Alembic applies the recovery-job migration, then restart `northstar`. Starting the configured instance without these credentials is rejected before the power call, so the UI cannot silently skip recovery.

Example server-side setup (replace `clouds.yaml` with the account's securely obtained OpenStack configuration; do not paste secrets into shell history):

```bash
sudo install -d -o root -g northstar -m 0750 /etc/northstar/openstack
sudo install -o root -g northstar -m 0640 /secure/path/clouds.yaml /etc/northstar/openstack/clouds.yaml
sudoedit /etc/northstar/northstar.env
sudo systemctl restart northstar
```

The OVH page displays job stages and per-proxy outcomes. `Proxy ready` is shown only when all 15 authenticated checks return the corresponding Floating IP as their IPv4 exit address.

To update the application, copy the updated project checkout to the server and rerun the installer. Existing environment values are backed up and retained; the existing database and administrator are not reset. The deployment uses an initial baseline Alembic migration for a fresh database. For a database already containing production data, take and test a backup and compare its schema before using this deployment flow.
