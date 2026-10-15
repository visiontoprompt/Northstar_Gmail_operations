"""GoDaddy account credentials and authoritative DNS management."""

from __future__ import annotations

import re
import uuid
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, GoDaddyAccount
from .security import SecretCipher

API_BASES = {
    "production": "https://api.godaddy.com",
    "ote": "https://api.ote-godaddy.com",
}
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
RECORD_TYPES = {"A", "AAAA", "CAA", "CNAME", "MX", "NS", "SOA", "SRV", "TXT"}
WRITABLE_TYPES = RECORD_TYPES - {"NS", "SOA"}


class GoDaddyError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _domain(value: str) -> str:
    cleaned = value.strip().rstrip(".").lower()
    if not DOMAIN_RE.fullmatch(cleaned):
        raise GoDaddyError("Enter a valid domain name")
    return cleaned


def _name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 255 or any(char in cleaned for char in "/?#"):
        raise GoDaddyError("DNS record name is invalid")
    return quote(cleaned, safe="")


class GoDaddyClient:
    def __init__(self, environment: str, key: str, secret: str, transport: httpx.BaseTransport | None = None):
        if environment not in API_BASES:
            raise GoDaddyError("Unsupported GoDaddy environment")
        if not key.strip():
            raise GoDaddyError("GoDaddy API key or Personal Access Token is required")
        self.base = API_BASES[environment]
        self.is_pat = not secret.strip()
        self.http = httpx.Client(timeout=20, trust_env=False, transport=transport, headers={
            "Authorization": f"Bearer {key.strip()}" if self.is_pat else f"sso-key {key.strip()}:{secret.strip()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> GoDaddyClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def call(self, method: str, path: str, *, params: dict | None = None, body: object = None) -> object:
        try:
            response = self.http.request(method, f"{self.base}{path}", params=params, json=body)
            response.raise_for_status()
            return response.json() if response.content else None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            code = ""
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    code = str(payload.get("code") or "").strip()
                    detail = str(payload.get("message") or "").strip()
            except (ValueError, TypeError):
                pass
            detail = detail[:300]
            message = {401: "GoDaddy rejected these API credentials.", 403: "The GoDaddy credential lacks the required Domains read or DNS update permissions.", 404: "GoDaddy could not find a GoDaddy-hosted DNS zone for this domain. Confirm the PAT owns the domain and that it uses GoDaddy nameservers.", 409: "GoDaddy blocked this DNS operation because the domain or zone is in a state that does not allow it.", 429: "GoDaddy rate-limited this account. Wait before trying again."}.get(status, f"GoDaddy API returned HTTP {status}.")
            if code:
                message += f" Code: {code}."
            if detail:
                message += f" GoDaddy says: {detail}"
            raise GoDaddyError(message, status_code=status) from exc
        except httpx.HTTPError as exc:
            raise GoDaddyError("Could not reach the GoDaddy API.") from exc

    def list_domains(self) -> list[dict]:
        output: list[dict] = []
        marker = None
        for _ in range(100):
            # The domain list omits nameservers unless explicitly requested.
            # We use them in the UI to distinguish registrar ownership from
            # authoritative DNS hosting before offering zone controls.
            params = {"limit": 1000, "includes": ["nameServers"]}
            if marker:
                params["marker"] = marker
            page = self.call("GET", "/v1/domains", params=params)
            if not isinstance(page, list):
                raise GoDaddyError("GoDaddy returned an invalid domain list")
            output.extend(item for item in page if isinstance(item, dict) and isinstance(item.get("domain"), str))
            if len(page) < 1000:
                return output
            marker = page[-1].get("domain")
            if not marker:
                break
        return output

    def list_records(self, domain: str) -> list[dict]:
        zone = _domain(domain)
        if not self.is_pat:
            raise GoDaddyError("This saved classic key can list domains, but reading the complete DNS zone requires a GoDaddy Personal Access Token (PAT). Edit this account, enter the PAT as the API key, and leave API secret blank.")
        self._assert_dns_eligible(zone)
        records: list[dict] = []
        page = 1
        while True:
            response = self.call("GET", f"/v3/domains/zones/{quote(zone, safe='')}/dns-records", params={"page": page, "pageSize": 100})
            if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                raise GoDaddyError("GoDaddy returned an invalid DNS zone")
            items = [item for item in response["items"] if isinstance(item, dict)]
            records.extend(items)
            if len(items) < 100:
                return records
            page += 1
            if page > 100:
                raise GoDaddyError("The DNS zone is too large to retrieve safely")

    def _require_pat(self) -> None:
        if not self.is_pat:
            raise GoDaddyError("DNS record changes require a GoDaddy Personal Access Token (PAT). Edit this account, enter the PAT as the API key, and leave API secret blank.")

    def _assert_dns_eligible(self, domain: str) -> None:
        registered = next((item for item in self.list_domains() if str(item.get("domain", "")).lower() == domain), None)
        if not registered:
            return
        state = str(registered.get("status") or "").strip().upper()
        if state and state != "ACTIVE":
            expiry = registered.get("expires")
            expiry_note = f" The listed expiration date is {expiry}." if expiry else ""
            raise GoDaddyError(f"DNS is unavailable because GoDaddy reports this domain as {state}.{expiry_note} Contact GoDaddy to confirm whether it can be restored or must be registered again.", status_code=409)
        nameservers = registered.get("nameServers") or []
        if nameservers and not all(str(ns).rstrip(".").lower().endswith((".domaincontrol.com", ".secureserver.net")) for ns in nameservers):
            raise GoDaddyError(f"DNS is hosted by external nameservers ({', '.join(str(ns) for ns in nameservers)}); manage this zone at that DNS provider.", status_code=409)

    def add_record(self, domain: str, record: dict) -> None:
        self._validate_record(record)
        self._require_pat()
        zone = _domain(domain)
        self._assert_dns_eligible(zone)
        self.call("POST", f"/v3/domains/zones/{quote(zone, safe='')}/dns-records", body=record)

    def replace_group(self, domain: str, record_type: str, name: str, records: list[dict]) -> None:
        record_type = record_type.upper()
        if record_type not in WRITABLE_TYPES:
            raise GoDaddyError("This DNS record type is read-only or unsupported")
        for record in records:
            self._validate_record(record)
            if record["type"].upper() != record_type or record["name"] != name:
                raise GoDaddyError("Replacement records must have the selected type and name")
        self._require_pat()
        zone = quote(_domain(domain), safe='')
        existing = [r for r in self.list_records(domain) if str(r.get("type", "")).upper() == record_type and r.get("name") == name]
        # Update matching records in place, add extras, then remove only surplus values.
        for index, record in enumerate(records):
            if index < len(existing) and existing[index].get("recordId"):
                record_id = quote(str(existing[index]["recordId"]), safe='')
                self.call("PUT", f"/v3/domains/zones/{zone}/dns-records/{record_id}", body=record)
            else:
                self.call("POST", f"/v3/domains/zones/{zone}/dns-records", body=record)
        for old in existing[len(records):]:
            if old.get("recordId"):
                self.call("DELETE", f"/v3/domains/zones/{zone}/dns-records/{quote(str(old['recordId']), safe='')}")

    def delete_group(self, domain: str, record_type: str, name: str) -> None:
        record_type = record_type.upper()
        if record_type not in WRITABLE_TYPES:
            raise GoDaddyError("This DNS record type is read-only or unsupported")
        self._require_pat()
        zone = quote(_domain(domain), safe='')
        existing = [r for r in self.list_records(domain) if str(r.get("type", "")).upper() == record_type and r.get("name") == name]
        if not existing:
            raise GoDaddyError("No matching DNS records were found")
        for record in existing:
            if record.get("recordId"):
                self.call("DELETE", f"/v3/domains/zones/{zone}/dns-records/{quote(str(record['recordId']), safe='')}")

    @staticmethod
    def _validate_record(record: dict) -> None:
        if not isinstance(record, dict) or str(record.get("type", "")).upper() not in WRITABLE_TYPES:
            raise GoDaddyError("DNS record type is invalid or read-only")
        if not isinstance(record.get("name"), str) or not record["name"].strip():
            raise GoDaddyError("DNS record name is required")
        if not isinstance(record.get("data"), str) or not record["data"].strip():
            raise GoDaddyError("DNS record value is required")
        ttl = record.get("ttl", 3600)
        if not isinstance(ttl, int) or not 600 <= ttl <= 86400:
            raise GoDaddyError("TTL must be between 600 and 86400 seconds")


def _credentials(row: GoDaddyAccount) -> dict[str, str]:
    try:
        return SecretCipher().decrypt(row.encrypted_credentials)
    except Exception as exc:  # noqa: BLE001
        raise GoDaddyError("Saved GoDaddy credentials could not be decrypted") from exc


def get_account(db: Session, organization_id: str, account_id: str) -> GoDaddyAccount:
    row = db.scalar(select(GoDaddyAccount).where(GoDaddyAccount.organization_id == organization_id, GoDaddyAccount.id == account_id))
    if not row:
        raise GoDaddyError("GoDaddy account not found")
    return row


def list_accounts(db: Session, organization_id: str) -> list[dict]:
    rows = db.scalars(select(GoDaddyAccount).where(GoDaddyAccount.organization_id == organization_id).order_by(GoDaddyAccount.label)).all()
    output = []
    for row in rows:
        credentials = _credentials(row) if row.encrypted_credentials else {}
        output.append({"id": row.id, "label": row.label, "environment": row.environment, "credentials_configured": bool(row.encrypted_credentials), "auth_type": "pat" if not credentials.get("api_secret") else "classic", "created_at": row.created_at, "updated_at": row.updated_at})
    return output


def save_account(db: Session, organization_id: str, actor_id: str, *, label: str, environment: str, api_key: str | None, api_secret: str | None, account_id: str | None = None) -> dict:
    if environment not in API_BASES:
        raise GoDaddyError("Choose Production or OTE environment")
    cleaned_label = " ".join(label.split())
    if not cleaned_label or len(cleaned_label) > 120:
        raise GoDaddyError("Account label must be between 1 and 120 characters")
    duplicate = db.scalar(select(GoDaddyAccount.id).where(GoDaddyAccount.organization_id == organization_id, GoDaddyAccount.label == cleaned_label, GoDaddyAccount.id != (account_id or "")))
    if duplicate:
        raise GoDaddyError("A GoDaddy account already uses that label")
    row = get_account(db, organization_id, account_id) if account_id else GoDaddyAccount(id=f"godaddy_{uuid.uuid4().hex[:20]}", organization_id=organization_id, label=cleaned_label, environment=environment, encrypted_credentials="")
    if account_id and (api_key is None or api_secret is None):
        credentials = _credentials(row)
        api_key = api_key or credentials["api_key"]
        api_secret = api_secret or credentials["api_secret"]
    if not api_key:
        raise GoDaddyError("Enter a GoDaddy API key or Personal Access Token")
    row.label, row.environment = cleaned_label, environment
    row.encrypted_credentials = SecretCipher().encrypt({"api_key": api_key.strip(), "api_secret": (api_secret or "").strip()})
    db.add(row)
    db.flush()
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=organization_id, actor_id=actor_id, action="godaddy.account_saved", target_type="godaddy_account", target_id=row.id, result="success", metadata_json={"label": row.label, "environment": row.environment, "credentials": "configured"}))
    db.commit()
    return {"id": row.id, "label": row.label, "environment": row.environment, "credentials_configured": True, "created_at": row.created_at, "updated_at": row.updated_at}


def remove_account(db: Session, organization_id: str, actor_id: str, account_id: str) -> None:
    row = get_account(db, organization_id, account_id)
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=organization_id, actor_id=actor_id, action="godaddy.account_removed", target_type="godaddy_account", target_id=row.id, result="success", metadata_json={"label": row.label}))
    db.delete(row)
    db.commit()


def create_client(db: Session, organization_id: str, account_id: str, transport: httpx.BaseTransport | None = None) -> GoDaddyClient:
    row = get_account(db, organization_id, account_id)
    creds = _credentials(row)
    return GoDaddyClient(row.environment, creds["api_key"], creds["api_secret"], transport)
