"""OVHcloud power controls for VPS services and Public Cloud instances."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, WorkspaceSettings
from .security import SecretCipher


ENDPOINTS = {
    "ovh-eu": "https://eu.api.ovh.com/1.0",
    "ovh-ca": "https://ca.api.ovh.com/1.0",
    "ovh-us": "https://api.us.ovhcloud.com/1.0",
}
RESOURCE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class OvhError(Exception):
    pass


def _safe_name(value: str) -> str:
    if not RESOURCE_NAME.fullmatch(value):
        raise OvhError("Invalid OVH resource identifier")
    return quote(value, safe="")


class OvhClient:
    def __init__(self, endpoint: str, credentials: dict[str, str], transport: httpx.BaseTransport | None = None):
        if endpoint not in ENDPOINTS:
            raise OvhError("Unsupported OVH API region")
        self.base = ENDPOINTS[endpoint]
        self.credentials = credentials
        self.http = httpx.Client(timeout=15, transport=transport, trust_env=False)
        self._clock_offset: int | None = None

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> OvhClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _timestamp(self) -> int:
        if self._clock_offset is None:
            try:
                response = self.http.get(f"{self.base}/auth/time")
                response.raise_for_status()
                self._clock_offset = int(response.json()) - int(time.time())
            except (httpx.HTTPError, ValueError) as exc:
                raise OvhError("Could not synchronize with the OVH API") from exc
        return int(time.time()) + self._clock_offset

    def call(self, method: str, path: str) -> object:
        if not path.startswith("/") or "?" in path:
            raise OvhError("Invalid OVH API path")
        url = f"{self.base}{path}"
        timestamp = str(self._timestamp())
        secret = self.credentials["ovh_application_secret"]
        consumer = self.credentials["ovh_consumer_key"]
        signed = "+".join((secret, consumer, method, url, "", timestamp))
        signature = "$1$" + hashlib.sha1(signed.encode("utf-8")).hexdigest()
        try:
            response = self.http.request(method, url, headers={
                "Accept": "application/json",
                "X-Ovh-Application": self.credentials["ovh_application_key"],
                "X-Ovh-Consumer": consumer,
                "X-Ovh-Timestamp": timestamp,
                "X-Ovh-Signature": signature,
            })
            response.raise_for_status()
            return response.json() if response.content else None
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            detail = ""
            try:
                payload = exc.response.json()
                if isinstance(payload, dict):
                    detail = str(payload.get("message") or payload.get("error") or "").strip()
            except (ValueError, TypeError):
                pass
            detail = detail[:300]
            if code == 401:
                suffix = f" OVH says: {detail}" if detail else ""
                raise OvhError(f"OVH authentication failed (HTTP 401). Verify the application key, secret, consumer key, and API region.{suffix}") from exc
            if code == 403:
                suffix = f" OVH says: {detail}" if detail else ""
                raise OvhError(f"OVH denied this API path (HTTP 403). The consumer key must explicitly allow this method and path.{suffix}") from exc
            if code == 404:
                suffix = f" OVH says: {detail}" if detail else ""
                raise OvhError(f"The OVH resource was not found or is outside the selected OVH region.{suffix}") from exc
            raise OvhError(f"OVH API returned HTTP {code}.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OvhError("OVH API could not be reached or returned an invalid response.") from exc

    def list_resources(self) -> dict:
        items: list[dict] = []
        errors: list[str] = []
        try:
            services = self.call("GET", "/vps")
            if not isinstance(services, list):
                raise OvhError("OVH returned an invalid VPS list.")
            for name in services:
                if not isinstance(name, str) or not RESOURCE_NAME.fullmatch(name):
                    continue
                try:
                    detail = self.call("GET", f"/vps/{_safe_name(name)}")
                    detail = detail if isinstance(detail, dict) else {}
                    items.append({"resource_type": "vps", "service_name": name, "instance_id": None,
                                  "name": detail.get("displayName") or name, "status": str(detail.get("state") or "UNKNOWN"),
                                  "ip": detail.get("ip"), "project_name": None})
                except OvhError as exc:
                    items.append({"resource_type": "vps", "service_name": name, "instance_id": None,
                                  "name": name, "status": "UNKNOWN", "ip": None, "project_name": None, "error": str(exc)})
        except OvhError as exc:
            errors.append(f"VPS: {exc}")
        try:
            projects = self.call("GET", "/cloud/project")
            if not isinstance(projects, list):
                raise OvhError("OVH returned an invalid project list.")
            for project in projects:
                if not isinstance(project, str) or not RESOURCE_NAME.fullmatch(project):
                    continue
                try:
                    instances = self.call("GET", f"/cloud/project/{_safe_name(project)}/instance")
                    if not isinstance(instances, list):
                        raise OvhError("OVH returned an invalid instance list.")
                    for instance in instances:
                        if not isinstance(instance, dict) or not isinstance(instance.get("id"), str):
                            continue
                        items.append({"resource_type": "instance", "service_name": project,
                                      "instance_id": instance["id"], "name": instance.get("name") or instance["id"],
                                      "status": str(instance.get("status") or "UNKNOWN"),
                                      "ip": next((address.get("ip") for address in (instance.get("ipAddresses") or []) if isinstance(address, dict) and address.get("ip")), None),
                                      "project_name": project})
                except OvhError as exc:
                    errors.append(f"Project {project}: {exc}")
        except OvhError as exc:
            errors.append(f"Public Cloud: {exc}")
        return {"items": items, "errors": errors}

    def power(self, resource_type: str, service_name: str, instance_id: str | None, action: str) -> object:
        service = _safe_name(service_name)
        if action not in {"start", "stop"}:
            raise OvhError("Unsupported power action")
        if resource_type == "vps" and not instance_id:
            return self.call("POST", f"/vps/{service}/{action}")
        if resource_type == "instance" and instance_id:
            return self.call("POST", f"/cloud/project/{service}/instance/{_safe_name(instance_id)}/{action}")
        raise OvhError("The OVH resource type and identifier do not match")


def configured_client(db: Session, organization_id: str) -> OvhClient:
    row = db.scalar(select(WorkspaceSettings).where(WorkspaceSettings.organization_id == organization_id))
    if not row or not row.ovh_credentials_encrypted:
        raise OvhError("Add OVH API credentials in Settings first.")
    try:
        credentials = SecretCipher().decrypt(row.ovh_credentials_encrypted)
        if not isinstance(credentials, dict) or not all(isinstance(credentials.get(key), str) and credentials[key] for key in ("ovh_application_key", "ovh_application_secret", "ovh_consumer_key")):
            raise ValueError("Missing OVH credential component")
        return OvhClient(row.ovh_endpoint, credentials)
    except Exception as exc:
        raise OvhError("The saved OVH credentials could not be read.") from exc


def record_power_action(db: Session, organization_id: str, actor_id: str, resource_type: str, service_name: str, instance_id: str | None, action: str) -> None:
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:12]}", organization_id=organization_id, actor_id=actor_id,
                      action=f"ovh.{resource_type}.{action}", target_type=f"ovh_{resource_type}",
                      target_id=f"{service_name}/{instance_id}" if instance_id else service_name, result="accepted",
                      metadata_json={"provider": "OVHcloud", "action": action}))
    db.commit()
