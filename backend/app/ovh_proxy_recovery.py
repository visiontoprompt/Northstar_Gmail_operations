"""Restart-safe OVH Floating IP recovery using the external OpenStack Neutron API."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .db import SessionLocal
from .models import OvhProxyRecoveryJob
from .ovh_service import configured_client

logger = logging.getLogger("gmail_ops.ovh_proxy_recovery")

# These are the operator-provided expected bindings. They are validation data,
# not credentials. Neutron port IDs are discovered and durably saved at runtime.
EXPECTED_FIXED_IPS = {
    "40.160.98.129": "10.1.1.95",
    "40.160.8.119": "10.1.2.106",
    "40.160.93.125": "10.1.1.135",
    "40.160.93.174": "10.1.0.113",
    "40.160.7.221": "10.1.3.219",
    "40.160.14.112": "10.1.2.83",
    "40.160.98.20": "10.1.1.11",
    "40.160.4.180": "10.1.2.165",
    "40.160.69.151": "10.1.0.153",
    "135.148.233.51": "10.1.0.211",
    "40.160.7.174": "10.1.3.103",
    "40.160.98.162": "10.1.3.9",
    "40.160.11.22": "10.1.3.160",
    "40.160.98.5": "10.1.0.247",
    "40.160.14.164": "10.1.3.67",
}
ACTIVE_STATES = {"QUEUED", "STARTING", "WAITING", "PROBING", "RECOVERING", "VERIFYING", "STOPPING"}
_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


class RecoveryError(Exception):
    pass


def recovery_target(service_name: str, instance_id: str) -> bool:
    settings = get_settings()
    return settings.ovh_proxy_recovery_enabled and service_name == settings.ovh_proxy_recovery_project_id and instance_id == settings.ovh_proxy_recovery_instance_id


def recovery_configured() -> tuple[bool, str | None]:
    settings = get_settings()
    if not settings.ovh_proxy_recovery_username or not settings.ovh_proxy_recovery_password:
        return False, "Set OVH_PROXY_RECOVERY_USERNAME and OVH_PROXY_RECOVERY_PASSWORD in the server environment."
    import os
    from pathlib import Path
    config_path = os.environ.get("OS_CLIENT_CONFIG_FILE", "")
    if not config_path:
        return False, "Set OS_CLIENT_CONFIG_FILE in /etc/northstar/northstar.env to the OpenStack clouds.yaml path."
    cloud_file = Path(config_path)
    if not cloud_file.is_file():
        return False, "The OpenStack clouds.yaml set by OS_CLIENT_CONFIG_FILE does not exist on the application server."
    if not os.access(cloud_file, os.R_OK, effective_ids=True):
        return False, "The northstar service cannot read the OpenStack clouds.yaml set by OS_CLIENT_CONFIG_FILE."
    if cloud_file.stat().st_size == 0:
        return False, "The OpenStack clouds.yaml set by OS_CLIENT_CONFIG_FILE is empty."
    if not settings.openstack_cloud:
        return False, "Set OPENSTACK_CLOUD to the cloud name in clouds.yaml."
    return True, None


def _as_dict(resource: Any) -> dict[str, Any]:
    if isinstance(resource, dict):
        return resource
    if hasattr(resource, "to_dict"):
        return resource.to_dict()
    return {key: getattr(resource, key, None) for key in ("id", "floating_ip_address", "port_id", "fixed_ip_address", "status")}


def probe_proxy(ip: str, *, proxy_port: int, username: str, password: str, url: str, timeout: float = 9.0) -> dict[str, Any]:
    """Probe externally; do not include the credential-bearing proxy URL in errors."""
    start = time.monotonic()
    try:
        proxy = httpx.Proxy(f"http://{ip}:{proxy_port}", auth=(username, password))
        with httpx.Client(proxy=proxy, timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = client.get(url)
            if response.status_code == 407:
                return {"status": "AUTH_FAILED", "error": "Proxy authentication returned HTTP 407", "latency_ms": int((time.monotonic() - start) * 1000)}
            response.raise_for_status()
            body = response.text.strip()
            try:
                import json
                parsed = json.loads(body)
                exit_ip = str(parsed.get("ip", "")).strip() if isinstance(parsed, dict) else body
            except (ValueError, TypeError):
                exit_ip = body
            if exit_ip != ip:
                return {"status": "WRONG_EXIT_IP", "exit_ip": exit_ip[:64], "error": "Proxy exit IP did not match its Floating IP", "latency_ms": int((time.monotonic() - start) * 1000)}
            return {"status": "HEALTHY", "exit_ip": exit_ip, "error": None, "latency_ms": int((time.monotonic() - start) * 1000)}
    except httpx.ProxyError as exc:
        message = str(exc)
        if "407" in message or "Proxy Authentication Required" in message:
            status, error = "AUTH_FAILED", "Proxy authentication returned HTTP 407"
        elif isinstance(exc.__cause__, (httpx.TimeoutException, TimeoutError)) or "timed out" in message.lower():
            status, error = "TIMEOUT", "External proxy connection timed out"
        else:
            status, error = "CONNECTION_FAILED", "External connection to the proxy failed"
        return {"status": status, "error": error, "latency_ms": int((time.monotonic() - start) * 1000)}
    except httpx.TimeoutException:
        return {"status": "TIMEOUT", "error": "External proxy request timed out", "latency_ms": int((time.monotonic() - start) * 1000)}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 407:
            status, error = "AUTH_FAILED", "Proxy authentication returned HTTP 407"
        else:
            status, error = "ENDPOINT_ERROR", f"IP-check endpoint returned HTTP {exc.response.status_code}"
        return {"status": status, "error": error, "latency_ms": int((time.monotonic() - start) * 1000)}
    except httpx.HTTPError:
        return {"status": "CONNECTION_FAILED", "error": "External proxy connection failed", "latency_ms": int((time.monotonic() - start) * 1000)}


def _job_snapshot(job: OvhProxyRecoveryJob) -> dict[str, Any]:
    public_results = [{key: value for key, value in row.items() if key not in {"resource_id", "port_id", "fixed_ip"}} for row in (job.results or [])]
    return {"id": job.id, "status": job.status, "stage": job.stage, "message": job.message,
            "total_count": job.total_count, "completed_count": job.completed_count,
            "cancel_requested": job.cancel_requested, "results": public_results,
            "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat()}


def create_recovery_job(organization_id: str, service_name: str, instance_id: str, actor_id: str, *, start: bool = True) -> dict[str, Any]:
    settings = get_settings()
    resource_key = f"{service_name}/{instance_id}"
    job = OvhProxyRecoveryJob(id=f"ovh_recovery_{uuid.uuid4().hex}", organization_id=organization_id,
        resource_key=resource_key, lock_key=resource_key, service_name=service_name, instance_id=instance_id,
        status="QUEUED", stage="QUEUED", message="Start accepted; recovery is queued.",
        results=[], total_count=len(EXPECTED_FIXED_IPS), completed_count=0)
    with SessionLocal() as db:
        active = db.scalar(select(OvhProxyRecoveryJob).where(OvhProxyRecoveryJob.organization_id == organization_id,
            OvhProxyRecoveryJob.lock_key == resource_key))
        if active:
            raise RecoveryError(f"Recovery is already active (job {active.id}).")
        db.add(job)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RecoveryError("A start or recovery job is already active for this instance.") from exc
        snapshot = _job_snapshot(job)
        job_id = job.id
    if start:
        start_recovery_thread(job_id)
    return snapshot


def validate_openstack_config() -> None:
    connection = _open_neutron()
    connection.close()


def get_recovery_job(organization_id: str, job_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        job = db.scalar(select(OvhProxyRecoveryJob).where(OvhProxyRecoveryJob.organization_id == organization_id, OvhProxyRecoveryJob.id == job_id))
        return _job_snapshot(job) if job else None


def latest_recovery_job(organization_id: str, service_name: str, instance_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        job = db.scalar(select(OvhProxyRecoveryJob).where(OvhProxyRecoveryJob.organization_id == organization_id,
            OvhProxyRecoveryJob.resource_key == f"{service_name}/{instance_id}").order_by(OvhProxyRecoveryJob.created_at.desc()).limit(1))
        return _job_snapshot(job) if job else None


def fail_recovery_job(job_id: str, message: str) -> None:
    def finish(job):
        job.status = "FAILED"
        job.stage = "NEEDS_ATTENTION"
        job.message = message[:500]
        job.lock_key = None
    _mutate_job(job_id, finish)


def request_recovery_cancel(organization_id: str, service_name: str, instance_id: str, wait_seconds: float = 30) -> bool:
    key = f"{service_name}/{instance_id}"
    with SessionLocal() as db:
        job = db.scalar(select(OvhProxyRecoveryJob).where(OvhProxyRecoveryJob.organization_id == organization_id, OvhProxyRecoveryJob.lock_key == key))
        if not job:
            return True
        job.cancel_requested = True
        job.stage = "STOPPING"
        job.message = "Stop requested; finishing any in-flight Floating IP restore safely."
        db.commit()
        job_id = job.id
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(0.25)
        with SessionLocal() as db:
            job = db.get(OvhProxyRecoveryJob, job_id)
            if not job or job.lock_key is None:
                return True
    return False


def resume_recovery_jobs() -> None:
    with SessionLocal() as db:
        ids = list(db.scalars(select(OvhProxyRecoveryJob.id).where(OvhProxyRecoveryJob.lock_key.is_not(None), OvhProxyRecoveryJob.status.in_(ACTIVE_STATES))).all())
    for job_id in ids:
        start_recovery_thread(job_id)


def start_recovery_thread(job_id: str) -> None:
    with _threads_lock:
        existing = _threads.get(job_id)
        if existing and existing.is_alive():
            return
        thread = threading.Thread(target=_run_recovery_job, args=(job_id,), name=f"ovh-proxy-recovery-{job_id[-8:]}", daemon=True)
        _threads[job_id] = thread
        thread.start()


def _mutate_job(job_id: str, callback) -> bool:
    with SessionLocal() as db:
        job = db.get(OvhProxyRecoveryJob, job_id)
        if not job:
            return True
        callback(job)
        db.commit()
        return bool(job.cancel_requested)


def _cancelled(job_id: str) -> bool:
    with SessionLocal() as db:
        job = db.get(OvhProxyRecoveryJob, job_id)
        return not job or bool(job.cancel_requested)


def _instance_status(organization_id: str, project_id: str, instance_id: str) -> str:
    with SessionLocal() as db:
        with configured_client(db, organization_id) as client:
            inventory = client.list_resources()
    for item in inventory.get("items", []):
        if item.get("resource_type") == "instance" and item.get("service_name") == project_id and item.get("instance_id") == instance_id:
            return str(item.get("status", "UNKNOWN")).upper()
    return "UNKNOWN"


def _open_neutron():
    try:
        import openstack
        settings = get_settings()
        connection = openstack.connect(cloud=settings.openstack_cloud)
        connection.authorize()
        if connection.current_project_id != settings.ovh_proxy_recovery_project_id:
            connection.close()
            raise RecoveryError("OpenStack cloud configuration targets a different project; no Floating IP was changed.")
        region = str(getattr(connection.config, "region_name", "") or "")
        if region.casefold() != settings.ovh_proxy_recovery_region.casefold():
            connection.close()
            raise RecoveryError("OpenStack cloud configuration targets a different region; no Floating IP was changed.")
        return connection
    except RecoveryError:
        raise
    except Exception as exc:  # avoid leaking SDK config/auth exception details
        logger.error("Could not initialize OpenStack Neutron client; exception detail redacted")
        raise RecoveryError("OpenStack cloud configuration is unavailable; check the protected clouds.yaml and server environment.") from exc


def _list_fips(connection) -> list[dict[str, Any]]:
    try:
        return [_as_dict(item) for item in connection.network.ips()]
    except Exception as exc:
        logger.exception("Could not list Floating IP resources")
        raise RecoveryError("Neutron could not list Floating IPs; verify cloud permissions and region.") from exc


def _save_initial_bindings(job_id: str, fips: list[dict[str, Any]], expected_instance_id: str, get_port) -> list[dict[str, Any]]:
    by_address = {str(item.get("floating_ip_address") or ""): item for item in fips}
    saved = []
    for address, expected_fixed_ip in EXPECTED_FIXED_IPS.items():
        item = by_address.get(address)
        if not item or not item.get("id"):
            raise RecoveryError(f"Floating IP {address} was not found; no addresses were detached.")
        if str(item.get("fixed_ip_address") or "") != expected_fixed_ip:
            raise RecoveryError(f"Floating IP {address} did not match expected fixed IP {expected_fixed_ip}; no addresses were detached.")
        if not item.get("port_id"):
            raise RecoveryError(f"Floating IP {address} has no original port binding; no addresses were detached.")
        try:
            port = _as_dict(get_port(str(item["port_id"])))
        except Exception as exc:
            raise RecoveryError(f"Original port for Floating IP {address} could not be validated; no addresses were detached.") from exc
        fixed_ips = port.get("fixed_ips") or []
        port_fixed_ips = {str(value.get("ip_address")) for value in fixed_ips if isinstance(value, dict)}
        if str(port.get("device_id") or "") != expected_instance_id or expected_fixed_ip not in port_fixed_ips:
            raise RecoveryError(f"Original port for Floating IP {address} does not belong to the configured instance and expected fixed IP; no addresses were detached.")
        saved.append({"floating_ip": address, "resource_id": str(item["id"]), "port_id": str(item["port_id"]),
                      "fixed_ip": expected_fixed_ip, "status": "PENDING", "attempts": 0, "error": None, "exit_ip": None})
    with SessionLocal() as db:
        job = db.get(OvhProxyRecoveryJob, job_id)
        if not job:
            raise RecoveryError("Recovery job record is missing.")
        job.results = saved
        job.total_count = len(saved)
        job.stage = "PROBING"
        job.message = "Saved and validated all Floating IP IDs and original port/private-IP bindings."
        db.commit()
    return saved


def _update_result(job_id: str, result: dict[str, Any], status: str | None = None) -> None:
    with SessionLocal() as db:
        job = db.get(OvhProxyRecoveryJob, job_id)
        if not job:
            return
        rows = list(job.results or [])
        for index, row in enumerate(rows):
            if row.get("floating_ip") == result.get("floating_ip"):
                rows[index] = {**row, **result}
                break
        job.results = rows
        job.completed_count = sum(1 for row in rows if row.get("status") in {"HEALTHY", "AUTH_FAILED", "WRONG_EXIT_IP", "FAILED", "RESTORED"})
        if status:
            job.status = status
        db.commit()


def _probe_with_retries(ip: str) -> dict[str, Any]:
    settings = get_settings()
    latest = {}
    for attempt in range(1, max(1, settings.ovh_proxy_recovery_probe_attempts) + 1):
        latest = probe_proxy(ip, proxy_port=settings.ovh_proxy_recovery_port,
            username=settings.ovh_proxy_recovery_username or "", password=settings.ovh_proxy_recovery_password or "",
            url=settings.ovh_proxy_recovery_ip_check_url)
        latest["attempts"] = attempt
        if latest["status"] in {"HEALTHY", "AUTH_FAILED", "WRONG_EXIT_IP"}:
            return latest
        if attempt < max(1, settings.ovh_proxy_recovery_probe_attempts):
            time.sleep(min(2 * attempt, 5))
    return latest


def _wait_active(connection, floating_id: str, deadline_seconds: float = 60) -> bool:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            item = _as_dict(connection.network.get_ip(floating_id))
            if str(item.get("status", "")).upper() == "ACTIVE" and item.get("port_id"):
                return True
        except Exception:
            logger.warning("Could not read Floating IP status during recovery")
        time.sleep(2)
    return False


def _reassociate(connection, row: dict[str, Any]) -> None:
    floating_id, port_id, fixed_ip = row["resource_id"], row["port_id"], row["fixed_ip"]
    # Only modify the saved Floating IP association. Never create/delete IPs,
    # ports, NICs, or touch any server fixed/public interface configuration.
    connection.network.update_ip(floating_id, port_id=None)
    connection.network.update_ip(floating_id, port_id=port_id, fixed_ip_address=fixed_ip)
    if not _wait_active(connection, floating_id):
        raise RecoveryError("Floating IP did not report ACTIVE before the status deadline.")


def _restore(connection, row: dict[str, Any]) -> bool:
    for attempt in range(1, 4):
        try:
            current = _as_dict(connection.network.get_ip(row["resource_id"]))
            if current.get("port_id") == row["port_id"] and current.get("fixed_ip_address") == row["fixed_ip"]:
                return _wait_active(connection, row["resource_id"], 15)
            connection.network.update_ip(row["resource_id"], port_id=row["port_id"], fixed_ip_address=row["fixed_ip"])
            if _wait_active(connection, row["resource_id"], 20):
                return True
        except Exception:
            logger.error("Failed restoring saved Neutron binding for Floating IP %s (attempt %s); exception detail redacted", row.get("floating_ip"), attempt)
        time.sleep(attempt)
    return False


def _run_recovery_job(job_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(OvhProxyRecoveryJob, job_id)
        if not job:
            return
        organization_id, project_id, instance_id = job.organization_id, job.service_name, job.instance_id
    try:
        _mutate_job(job_id, lambda job: (setattr(job, "status", "STARTING"), setattr(job, "stage", "WAITING"), setattr(job, "message", "Waiting for the OVH instance to become ready.")))
        deadline = time.monotonic() + settings.ovh_proxy_recovery_readiness_timeout_seconds
        while time.monotonic() < deadline:
            if _cancelled(job_id):
                raise InterruptedError("Stop requested before recovery changes began.")
            state = _instance_status(organization_id, project_id, instance_id)
            if state in {"ACTIVE", "RUNNING"}:
                break
            time.sleep(max(2, settings.ovh_proxy_recovery_poll_seconds))
        else:
            raise RecoveryError("OVH instance readiness timed out; no Floating IP was detached.")
        _mutate_job(job_id, lambda job: (setattr(job, "status", "WAITING"), setattr(job, "stage", "NETWORK_GRACE"), setattr(job, "message", "Instance is ready; allowing networking and Squid a bounded startup grace period.")))
        grace_deadline = time.monotonic() + max(0, settings.ovh_proxy_recovery_network_grace_seconds)
        while time.monotonic() < grace_deadline:
            if _cancelled(job_id):
                raise InterruptedError("Stop requested during the bounded network startup grace period.")
            time.sleep(min(1, grace_deadline - time.monotonic()))
        if _cancelled(job_id):
            raise InterruptedError("Stop requested before Floating IP recovery began.")
        connection = _open_neutron()
        try:
            current_job = get_recovery_job(organization_id, job_id)
            rows = _save_initial_bindings(job_id, _list_fips(connection), instance_id, connection.network.get_port) if not current_job.get("results") else current_job["results"]
            for row in rows:
                if _cancelled(job_id):
                    raise InterruptedError("Stop requested; no further Floating IPs will be changed.")
                if row.get("status") == "HEALTHY":
                    continue
                initial = _probe_with_retries(row["floating_ip"])
                if initial["status"] == "HEALTHY":
                    _update_result(job_id, {"floating_ip": row["floating_ip"], **initial, "status": "HEALTHY", "recovered": False})
                    continue
                if initial["status"] not in {"TIMEOUT", "CONNECTION_FAILED"}:
                    _update_result(job_id, {"floating_ip": row["floating_ip"], **initial, "recovered": False})
                    continue
                # Only persistent connection errors/timeouts are eligible.
                _mutate_job(job_id, lambda job: (setattr(job, "status", "RECOVERING"), setattr(job, "stage", "REBINDING"), setattr(job, "message", f"Recovering {row['floating_ip']} after persistent external connection failure.")))
                _update_result(job_id, {"floating_ip": row["floating_ip"], "status": "REBINDING", "attempts": initial.get("attempts", 0), "error": initial.get("error"), "exit_ip": None})
                try:
                    _reassociate(connection, row)
                except Exception as exc:
                    logger.error("Floating IP reassociation failed for %s; restoring saved binding (exception detail redacted)", row["floating_ip"])
                    restored = _restore(connection, row)
                    state = "RESTORED" if restored else "FAILED"
                    detail = "Reassociation failed; original binding was restored." if restored else "Reassociation and restoration both failed; operator action required."
                    _update_result(job_id, {"floating_ip": row["floating_ip"], "status": state, "error": detail, "restore_error": None if restored else "Saved port/fixed-IP restoration was unsuccessful."})
                    continue
                verified = _probe_with_retries(row["floating_ip"])
                final_status = verified["status"]
                _update_result(job_id, {"floating_ip": row["floating_ip"], **verified, "status": final_status, "recovered": final_status == "HEALTHY"})
            latest = get_recovery_job(organization_id, job_id)
            rows = latest.get("results", [])
            if _cancelled(job_id):
                raise InterruptedError("Stop requested; recovery stopped after restoring any in-flight binding.")
            healthy = sum(row.get("status") == "HEALTHY" for row in rows)
            all_ready = len(rows) == len(EXPECTED_FIXED_IPS) and healthy == len(EXPECTED_FIXED_IPS)
            def finish(job):
                job.status = "PROXY_READY" if all_ready else "FAILED"
                job.stage = "COMPLETE" if all_ready else "NEEDS_ATTENTION"
                job.message = f"Proxy ready · all {healthy} Floating IP proxies passed external authentication and exit-IP checks." if all_ready else f"Verification complete · {healthy}/{len(rows)} proxies are ready; inspect per-proxy results."
                job.lock_key = None
            _mutate_job(job_id, finish)
        finally:
            connection.close()
    except InterruptedError as exc:
        _mutate_job(job_id, lambda job: (setattr(job, "status", "CANCELLED"), setattr(job, "stage", "CANCELLED"), setattr(job, "message", str(exc)), setattr(job, "lock_key", None)))
    except Exception as exc:
        safe_message = str(exc) if isinstance(exc, RecoveryError) else "Recovery failed; see service logs for the redacted underlying error."
        logger.error("OVH Floating IP recovery job %s failed (exception detail redacted)", job_id)
        _mutate_job(job_id, lambda job: (setattr(job, "status", "FAILED"), setattr(job, "stage", "NEEDS_ATTENTION"), setattr(job, "message", safe_message[:500]), setattr(job, "lock_key", None)))
