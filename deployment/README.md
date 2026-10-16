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

The installer enables Playwright and installs Xvfb, Openbox, x11vnc, and noVNC/Websockify. Each authenticated employee gets a private virtual X display and VNC/WebSocket pair on loopback. The API service uses a private writable `/tmp` namespace because Xvfb creates its display lock files and sockets there; its child browser processes share that namespace. It checks processes launched by the API through their owned process handles so desktop startup does not depend on matching platform-specific process command lines. Listener checks read Linux's socket table without making short VNC connections. Nginx authorizes noVNC files without starting the desktop; only the authenticated viewer connection starts or reuses it. x11vnc startup diagnostics are written to the Northstar systemd journal; failed startup messages include whether the VNC listener opened and the managed process exit code. The browser observer launches that employee's headed Chrome windows only on their display; the app's desktop auth subrequest selects the matching WebSocket port after authenticating the employee. Browser-session history, session detail/control, and bulk login-job history/control are scoped to the employee who created them, while account access continues to follow assigned groups. This prevents another operator with access to the same account group from seeing or controlling the first operator's browser session. Closing the viewer does not close the operator's browser sessions. The VNC and WebSocket listeners bind to loopback only; Nginx protects desktop assets and routes the control socket to the authenticated employee's private viewer. Ports 5900 and 6080 are not opened in the firewall.

On an existing installation, update the checkout and rerun `sudo bash deployment/install-ubuntu.sh` so the per-operator WebSocket routing is installed in Nginx and the schema migration adds session ownership. The installer restarts the API after copying files so an already-running service cannot keep executing stale Python code. Existing browser sessions created before this release have no reliable operator attribution, so the migration places them under the workspace administrator for cleanup. The administrator should close those legacy sessions; operators can then reopen the accounts from their own Browser desktops. New sessions are isolated automatically.

## Import saved browser profiles

The Accounts action bar can upload a ZIP or RAR profile archive for one selected account. Settings → Backups lets an administrator upload one archive containing multiple account profile folders, choosing **Skip existing** or **Replace existing**. For a bulk import, name each folder by its account email, account ID, or profile ID; an optional common parent folder is accepted. The app extracts only folders matching existing, non-archived accounts into the configured `PROFILE_ROOT` and lists unmatched names in the result. A single-account upload replaces that account's profile. Imported profiles show **Profile imported** instead of **Never logged in**. The first session opens Gmail using that saved profile and the account's assigned proxy. If Gmail loads, the app marks the account authenticated; if Google requests sign-in, the app reports that requirement without deleting the imported files. A matching proxy does not guarantee Google will accept transferred cookies. Existing uploads made before this behavior are reconciled on application startup when their profile directory still exists.

Close affected browser sessions before importing. Locked or running profiles are reported and left unchanged. Replacement moves the previous directory to a timestamped safety copy under `PROFILE_ROOT/../profile-import-safety/` before installing the new one. Keep that directory in your backup plan until imports have been verified. The installer includes `unar` for RAR extraction and rerunning it installs the dependency on an existing server. Uploaded archives are limited to 20 GB and the extracted contents to 40 GB; allow additional temporary disk space during import. Profile archives can contain live cookies and other account data, so store and transfer them securely.

## Browser profile cache compaction

Northstar compacts a saved profile after its Chrome context has closed. Accounts → **Compact profiles** can also check selected accounts or all accounts accessible to the operator, showing real per-account results and bytes of cache files removed. Open or locked profiles are skipped. This does not change credentials or account authentication state.

The cleanup allowlist contains only directories named `Cache`, `Code Cache`, `GPUCache`, and `Media Cache` directly under a Chrome user-data directory or a standard profile directory (`Default`, `System Profile`, or `Profile N`). It never searches for arbitrary `cache` names. `Local State`, `Network/Cookies`, `Preferences`, `Local Storage`, `IndexedDB`, `Service Worker` (including its `CacheStorage`), and other unknown files remain untouched. The app checks both its session lock and live Chrome processes before removal and does not follow symlinked cache directories. The cache will gradually grow again as Chrome visits sites. No installer or schema change is needed for this feature; updating the application code is sufficient.

All browser windows share one desktop, so multiple concurrent sessions may overlap. Use Alt+Tab in the viewer to switch between windows. The application worker cap is set to 10; the account UI's configured concurrency remains the operational limit. Session state and profile locks continue to be managed by the existing app.

## Services and checks

```bash
systemctl status northstar northstar-xvfb northstar-openbox northstar-x11vnc northstar-websockify
journalctl -u northstar -f
sudo nginx -t
curl -fsS https://YOUR_HOSTNAME/api/v1/health
```

If the private browser viewer returns a 503, inspect the exact x11vnc startup failure and listener state with:

```bash
sudo journalctl -u northstar --since "5 minutes ago" -l --no-pager | grep -Ei 'private VNC|x11vnc|desktop'
sudo ss -ltnp | grep -E ':(2[0-9]{4})\b'
```

PostgreSQL and Redis listen locally. Uvicorn listens on `127.0.0.1:8000`; Nginx serves the production frontend and proxies authenticated app/API traffic over HTTPS. The installer keeps Uvicorn at one worker because current observers and dispatch state are process-local. It enables UFW for SSH/HTTP/HTTPS only and enables Certbot renewal.

## OVH US Floating IP recovery

Starting the configured OVH Public Cloud instance now creates a durable recovery job. It waits for the instance to become `ACTIVE`, allows a bounded network/Squid grace period, checks all 15 configured proxy addresses from the application server using proxy authentication and an IPv4 IP-check endpoint, then retries transient failures. It does not rebind healthy proxies, 407 authentication failures, or proxies returning an unexpected exit IP. For persistent connection failures only, the app validates every Floating IP against its expected fixed private IP, saves the Floating IP resource ID and original port/private-IP binding in PostgreSQL, then re-associates affected addresses sequentially. A failed re-association triggers restoration retries using that saved binding. Recovery state is resumed after API service restarts. Stop requests cancel between IPs and wait for an in-flight binding restoration before issuing power-off.

This is an automated workaround for the observed OVH networking behavior, not a confirmed explanation or fix for its cause. The app never allocates or deletes Floating IPs, changes fixed IPs, deletes ports/NICs, or changes the instance's main public IP. Do not change the expected IP-to-private-IP list without validating the network plan.

Configure these values on the server, not in the UI or source repository:

1. In `/etc/northstar/northstar.env`, set `OVH_PROXY_RECOVERY_USERNAME` and `OVH_PROXY_RECOVERY_PASSWORD` to the Squid credentials and retain the configured project, instance, region, and port values. The environment file should remain owned by `root:northstar` and mode `0640`.
2. Create `/etc/northstar/openstack/` with owner `root:northstar`, mode `0750`, then place a `clouds.yaml` built from the OVH OpenStack user/configuration there, owned by `root:northstar` and mode `0640`. OVH may provide an `openrc.sh` rather than a ready-made `clouds.yaml`; use its Keystone auth URL, user/domain, and project details to build the [OpenStack SDK configuration](https://docs.openstack.org/openstacksdk/latest/user/config/configuration.html), and enter the OpenStack user's password privately. Use the US project and set the cloud entry name to `northstar-ovh-us`; its `project_id` must be `239fa25938524e2f87575cc424a2b6fc` and `region_name` must be `US-EAST-VA-1`. Set `OS_CLIENT_CONFIG_FILE` to that file path in the environment file. Do not use OVH application/consumer keys as Keystone credentials.
3. Grant the OpenStack identity only what is needed to list/get existing Floating IPs, read their existing ports, and update the Floating IP `port_id` association (`network:list_floatingips`, `network:get_floatingip`, `network:get_port`, and `network:update_floatingip`, or OVH's equivalent project-level network permissions). The SDK verifies the configured project and region before it can change any association; the app also confirms each port belongs to the configured instance and contains the expected fixed IP.
4. Install/update the app so Alembic applies the recovery-job migration, then restart `northstar`. The OVH Start action remains available when recovery credentials are missing; the power request succeeds with a separate **proxy verification skipped** notice and no failed recovery job is created. The app never marks proxies ready without verification. Once the server credentials are configured, use **Verify/recover proxies** on the running instance to check every Floating IP and apply the bounded workaround where needed. The same action can verify an instance started directly from OVHcloud.

The protected file should have this shape; fill the auth fields from the OpenStack user and configuration for the US project, not from the OVH API application keys. Edit the file with `sudoedit` so the real password never appears in shell history:

```yaml
clouds:
  northstar-ovh-us:
    auth:
      auth_url: "<Keystone URL from the OVH OpenStack configuration>"
      username: "<OpenStack username>"
      password: "<OpenStack user password>"
      project_id: "239fa25938524e2f87575cc424a2b6fc"
      user_domain_name: "<user domain from OpenStack configuration>"
    region_name: "US-EAST-VA-1"
    interface: public
    identity_api_version: 3
```

Example server-side setup (replace `clouds.yaml` with the account's securely obtained OpenStack configuration; do not paste secrets into shell history):

```bash
sudo install -d -o root -g northstar -m 0750 /etc/northstar/openstack
sudo install -o root -g northstar -m 0640 /secure/path/clouds.yaml /etc/northstar/openstack/clouds.yaml
sudoedit /etc/northstar/northstar.env
sudo systemctl restart northstar
```

If **Verify/recover proxies** reports that `OS_CLIENT_CONFIG_FILE` is missing, the Squid username/password may already be loaded. Those credentials only authenticate the HTTP proxy; a separate OpenStack `clouds.yaml` is required to read and update Neutron Floating IP associations. Obtain an OpenStack user and configuration for the same OVH US Public Cloud project, then create the SDK file using those details. The application cannot safely create it from the OVH application/consumer keys or from the Squid password.

After installing the file, confirm the service can read it without printing its contents or secrets:

```bash
sudo -u northstar test -r /etc/northstar/openstack/clouds.yaml && echo 'OpenStack file readable'
sudo systemctl restart northstar
sudo systemctl is-active northstar
```

Keep `OS_CLIENT_CONFIG_FILE=/etc/northstar/openstack/clouds.yaml` and `OPENSTACK_CLOUD=northstar-ovh-us` in `/etc/northstar/northstar.env`, with the latter matching the cloud entry in the file. Use `sudoedit /etc/northstar/northstar.env` to correct them; do not paste passwords into terminal commands. The OVH page retains the last recovery job as history, including an earlier missing-Squid-credentials failure. Its timestamp identifies that older attempt; pressing **Verify/recover proxies** checks the current configuration and starts a new job after it succeeds.

The OVH page displays job stages and per-proxy outcomes. `Proxy ready` is shown only when all 15 authenticated checks return the corresponding Floating IP as their IPv4 exit address.

To update the application, copy the updated project checkout to the server and rerun the installer. Existing environment values are backed up and retained; the existing database and administrator are not reset. The deployment uses an initial baseline Alembic migration for a fresh database. For a database already containing production data, take and test a backup and compare its schema before using this deployment flow.

## Workspace backup and restore

Administrators can create or restore a workspace backup from **Settings → Backups**. Before creation, a dialog lets them select browser profiles and deployment configuration; both are included by default. Every archive contains every application database table (including encrypted account and provider credentials). The optional profile component contains the configured browser profile directory. The optional deployment component contains a runtime-secret snapshot and readable files such as `/etc/northstar/northstar.env` and the configured OpenStack `clouds.yaml`. Deployment secrets are included only inside the encrypted archive.

Generated archives are retained in `/var/lib/northstar/backups/` (or alongside the configured profile root) and listed in Settings → Backups with their size, creation time, record/table counts, and profile-file count. Administrators can download any saved archive with one click, without entering its passphrase; the passphrase is still required to decrypt or restore it. Older safety snapshots without catalog metadata show their size and creation time. Monitor free disk space and remove obsolete archives from protected server storage according to your retention policy; deleting a downloaded copy does not remove the server copy.

The Backups tab reports measured progress for the current stage: tables exported or restored, encrypted archive bytes, browser-profile files, and upload/download bytes. A stage with no measurable total shows an activity indicator rather than an invented percentage. Keep the page open until the download or restore completes.

Each archive uses a passphrase-derived key and AES-256-GCM integrity protection. Use a unique passphrase of at least 12 characters, store it separately from the downloaded `.nstar` file, and keep the file off the application server. The passphrase is never stored by Northstar and cannot be reset. Close browser sessions and wait for active account/newsletter jobs to finish before backup or restore. Allow several times the archive size in free disk space during restore for the uploaded archive, verification, live profiles, and safety snapshot.

Restore replaces the application database. It replaces the browser profile directory only when the archive includes profiles; otherwise it preserves the server's existing profile files. Database profile records may still refer to files that are not present on a different server, so some accounts may need to sign in again. Before making changes, Northstar creates a **full** safety snapshot encrypted with the same passphrase, retains it under `/var/lib/northstar/backups/`, and downloads it to the browser when the current admin session remains valid. If the archive includes deployment configuration, restore stages it under `/var/lib/northstar/restored-deployment-config-*/` with owner-only permissions; it never overwrites live system configuration. On a new server, review and install that configuration securely before starting the restored database. In particular, preserve the source `SECRET_KEY` separately if deployment configuration was excluded, or existing encrypted credential records cannot be decrypted. `clouds.yaml` contains OpenStack credentials and must retain restrictive permissions.

The installer scopes Nginx's 20 GB request limit to the admin-only backup routes; the rest of the API keeps its normal upload limit. Rerun `deployment/install-ubuntu.sh` after updating this release so Nginx receives the backup upload configuration.
