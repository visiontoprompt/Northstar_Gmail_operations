import os
import re
import socket
import uuid
import base64
import json
import time
from urllib.request import ProxyHandler, Request, build_opener
from dataclasses import dataclass
from datetime import timedelta, timezone
from logging import getLogger
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from .config import get_settings
from .contacts import build_google_contacts_csv, contact_file_checksum, new_contact_file_id, parse_emails, validate_contact_file, write_contact_file, write_contacts_upload
from .addons import select_addon_candidates
from .db import SessionLocal
from .models import AccountGroup, AccountSecret, AuditEvent, BrowserProfile, BrowserSession, ContactFile, CredentialRevision, GmailAccount, ImportOperation, JobLock, LoginJob, LoginJobItem, OperationDraft, ProxyInventory, utcnow
from .schemas import AccountStructuredInput, BulkPreviewResponse, PreviewRow
from .security import SecretCipher, mask_recovery
from .runtime import close_local_browser, launch_local_browser, local_browser_is_alive, remove_local_profile, resolve_browser_executable
from .observer import BrowserObserver
from .login_policy import PageObservation, classify_page
from .proxy import ProxyValidationError, parse_proxy, proxy_from_payload
from .worker.locks import RedisLocks, account_lock_key
from .worker.queue import RedisQueue
from urllib.parse import urlparse

logger = getLogger(__name__)

WORKER_PREFIX = "worker-"
API_WORKER_ID = "orchestrator-api"
OPERATION_TYPES = {"contacts", "security", "labels", "apps", "mail", "addons", "alerts"}
FORBIDDEN_OPERATION_KEY_PARTS = ("password", "recovery", "2fa", "totp", "cookie", "token", "secret")
_UNSET = object()


def _worker_token() -> str:
    return f"{WORKER_PREFIX}{socket.gethostname()}_{os.getpid()}"

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ImportValidationError(ValueError):
    pass


class BrowserLaunchError(ImportValidationError):
    """Raised when the local Chrome process could not be started. Nothing is
    persisted for the session, so a later attempt can retry cleanly."""


def _bump_job(db: Session, job: LoginJob, **deltas: int) -> None:
    values = {}
    for field, delta in deltas.items():
        if delta:
            values[field] = getattr(LoginJob, field) + delta
    if values:
        db.execute(update(LoginJob).where(LoginJob.id == job.id).values(**values))
        db.flush()
        db.refresh(job)


@dataclass
class ParsedLine:
    row_number: int
    email: str
    password: str
    recovery: str
    two_fa: str


def parse_import_lines(raw_lines: str) -> tuple[list[ParsedLine], list[PreviewRow]]:
    parsed: list[ParsedLine] = []
    rows: list[PreviewRow] = []
    for row_number, raw in enumerate(raw_lines.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) != 4 or any(not part.strip() for part in parts):
            rows.append(PreviewRow(row_number=row_number, valid=False, error="Use email:password:recovery:2fa"))
            continue
        email, password, recovery, two_fa = (part.strip() for part in parts)
        normalized_email = email.lower()
        if not EMAIL_RE.match(normalized_email):
            rows.append(PreviewRow(row_number=row_number, valid=False, error="Enter a valid email address"))
            continue
        parsed.append(ParsedLine(row_number, normalized_email, password, recovery, two_fa))
        rows.append(PreviewRow(row_number=row_number, email=normalized_email, recovery_masked=mask_recovery(recovery), two_fa_configured=True, valid=True))
    return parsed, rows


def preview_import(db: Session, organization_id: str, group_id: str, raw_lines: str) -> BulkPreviewResponse:
    parsed, rows = parse_import_lines(raw_lines)
    group = db.scalar(select(AccountGroup).where(AccountGroup.id == group_id, AccountGroup.organization_id == organization_id))
    if not group:
        raise ImportValidationError("Choose a valid destination group")
    existing = set(db.scalars(select(GmailAccount.normalized_email).where(GmailAccount.organization_id == organization_id, GmailAccount.normalized_email.in_([line.email for line in parsed]), GmailAccount.lifecycle_status != "ARCHIVED")).all())
    seen: set[str] = set()
    for row in rows:
        if not row.valid or not row.email:
            continue
        if row.email in existing or row.email in seen:
            row.duplicate = True
            row.valid = False
            row.error = "Account already exists in this organization" if row.email in existing else "Duplicate in this import"
        seen.add(row.email)
    valid_rows = sum(row.valid for row in rows)
    duplicate_rows = sum(row.duplicate for row in rows)
    return {"total_rows": len(rows), "valid_rows": valid_rows, "duplicate_rows": duplicate_rows, "invalid_rows": len(rows) - valid_rows - duplicate_rows, "rows": rows}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def profile_folder_name(organization_id: str, email: str) -> str:
    """Permission-directory name for an account's saved Chrome profile.

    The on-disk profile lives under ``PROFILE_ROOT/<profile_folder_name>`` so
    engineers can find a saved session by the account name. The org id is
    appended so the same inbox in two organizations never shares a folder.
    """
    local = (email or "").strip().lower()
    local = re.sub(r"[^a-z0-9@._+\-]", "_", local)
    local = re.sub(r"_+", "_", local).strip("._@+-")
    if len(local) > 100:
        local = local[:100].rstrip("._@+-")
    org = re.sub(r"[^a-z0-9]", "_", organization_id.lower()).strip("_")
    if len(org) > 24:
        org = org[:24].rstrip("_")
    if not org:
        return local
    return f"{local}__{org}"


def _add_credential_revision(db: Session, organization_id: str, account_id: str, payload: dict, recovery_hint: str, two_fa_configured: bool, actor_id: str, version: int = 1) -> None:
    db.add(CredentialRevision(id=_new_id("credrev"), organization_id=organization_id, account_id=account_id, version=version, secret_ref=f"secret://account/{account_id}/v{version}", encrypted_payload=SecretCipher().encrypt(payload), recovery_hint=recovery_hint, two_fa_configured=two_fa_configured, status="ACTIVE", created_by=actor_id))


def _next_secret_version(db: Session, organization_id: str, account_id: str) -> int:
    return (db.scalar(select(func.max(CredentialRevision.version)).where(CredentialRevision.organization_id == organization_id, CredentialRevision.account_id == account_id)) or 0) + 1


def _reactivate_account(db: Session, organization_id: str, actor_id: str, account: GmailAccount, group: AccountGroup, email: str, password: str, recovery: str, two_fa: str, proxy_label: str | None, proxy_region: str | None, proxy_value: str | None = None) -> None:
    """Revive an ARCHIVED account (soft-deleted row is retained for policy)
    with fresh credential material and a reset profile for a clean login."""
    account.group_id = group.id
    account.lifecycle_status = "SETUP_REQUIRED"
    account.readiness_status = "READY_FOR_LOGIN"
    account.login_status = "NOT_STARTED"
    account.gmail_status = "NOT_CHECKED"
    account.recovery_hint = mask_recovery(recovery)
    account.two_fa_configured = bool(two_fa)
    try:
        proxy = parse_proxy(proxy_value)
    except ProxyValidationError as exc:
        raise ImportValidationError(str(exc)) from exc
    account.proxy_id = proxy.proxy_id if proxy else None
    account.proxy_label = proxy_label or (proxy.display_endpoint if proxy else None)
    account.proxy_region = proxy_region
    account.proxy_status = "ASSIGNED" if proxy or proxy_label or proxy_region else "UNASSIGNED"
    account.profile_status = "RESERVED"
    payload: dict = {"password": password, "recovery": recovery, "2fa": two_fa}
    if proxy:
        payload["proxy"] = proxy.encrypted_payload()
        _upsert_proxy_inventory(db, organization_id, proxy, account.proxy_label, account.proxy_region)
    secret = account.secret
    if secret is None:
        secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary")
    secret.encrypted_payload = SecretCipher().encrypt(payload)
    db.add(secret)
    _add_credential_revision(db, organization_id, account.id, payload, account.recovery_hint, account.two_fa_configured, actor_id, version=_next_secret_version(db, organization_id, account.id))
    profile = account.browser_profile
    if profile:
        profile.status = "RESERVED"
        profile.lock_state = "UNLOCKED"
        profile.active_session_id = None
        profile.authenticated_at = None
        profile.last_opened_at = None
        profile.last_closed_at = None
        db.add(profile)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.reactivated", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"group_id": group.id, "email_domain": email.split("@", 1)[1], "source": "re_add", "credentials_renewed": True}))


def add_account(db: Session, organization_id: str, actor_id: str, data: AccountStructuredInput) -> GmailAccount:
    group = db.scalar(select(AccountGroup).where(AccountGroup.id == data.group_id, AccountGroup.organization_id == organization_id))
    if not group:
        raise ImportValidationError("Choose a valid destination group")
    email = data.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ImportValidationError("Enter a valid email address")
    existing = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.normalized_email == email).with_for_update())
    if existing and existing.lifecycle_status != "ARCHIVED":
        raise ImportValidationError("An account with this email already exists")
    if existing:
        _reactivate_account(db, organization_id, actor_id, existing, group, email, data.password, data.recovery, data.two_fa, data.proxy_label, data.proxy_region, data.proxy)
        db.commit()
        db.refresh(existing)
        return existing
    try:
        proxy = parse_proxy(data.proxy)
    except ProxyValidationError as exc:
        raise ImportValidationError(str(exc)) from exc
    account_id = _new_id("acct")
    account = GmailAccount(id=account_id, organization_id=organization_id, group_id=group.id, email=email, normalized_email=email, lifecycle_status="SETUP_REQUIRED", readiness_status="READY_FOR_LOGIN", recovery_hint=mask_recovery(data.recovery), two_fa_configured=bool(data.two_fa), proxy_id=proxy.proxy_id if proxy else None, proxy_label=data.proxy_label or (proxy.display_endpoint if proxy else None), proxy_region=data.proxy_region, proxy_status="ASSIGNED" if proxy or data.proxy_label or data.proxy_region else "UNASSIGNED", profile_id=profile_folder_name(organization_id, email), profile_storage_ref=f"profiles/{organization_id}/{account_id}", profile_status="RESERVED")
    secret_payload: dict = {"password": data.password, "recovery": data.recovery, "2fa": data.two_fa}
    if proxy:
        secret_payload["proxy"] = proxy.encrypted_payload()
    secret = AccountSecret(id=_new_id("secret"), account_id=account_id, secret_ref=f"secret://account/{account_id}/primary", encrypted_payload=SecretCipher().encrypt(secret_payload))
    db.add(account)
    db.add(secret)
    if proxy:
        _upsert_proxy_inventory(db, organization_id, proxy, account.proxy_label, account.proxy_region)
    _add_credential_revision(db, organization_id, account_id, {"password": data.password, "recovery": data.recovery, "2fa": data.two_fa}, account.recovery_hint, account.two_fa_configured, actor_id)
    db.add(BrowserProfile(id=account.profile_id, organization_id=organization_id, account_id=account_id, storage_reference=account.profile_storage_ref, status="RESERVED", lock_state="UNLOCKED"))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.imported", target_type="gmail_account", target_id=account_id, result="success", metadata_json={"group_id": group.id, "email_domain": email.split("@", 1)[1], "source": "single"}))
    db.commit()
    db.refresh(account)
    return account


def import_accounts(db: Session, organization_id: str, actor_id: str, group_id: str, raw_lines: str, idempotency_key: str | None = None) -> tuple[int, int, int, list[str]]:
    if idempotency_key:
        previous = db.scalar(select(ImportOperation).where(ImportOperation.organization_id == organization_id, ImportOperation.idempotency_key == idempotency_key))
        if previous:
            return previous.imported, previous.duplicates, previous.invalid, list(previous.account_ids)
    preview = preview_import(db, organization_id, group_id, raw_lines)
    parsed, _ = parse_import_lines(raw_lines)
    valid_row_numbers = {row.row_number for row in preview["rows"] if row.valid}
    imported_ids: list[str] = []
    cipher = SecretCipher()
    for line in parsed:
        if line.row_number not in valid_row_numbers:
            continue
        existing = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.normalized_email == line.email).with_for_update())
        if existing:
            if existing.lifecycle_status != "ARCHIVED":
                continue
            _reactivate_account(db, organization_id, actor_id, existing, db.get(AccountGroup, group_id), line.email, line.password, line.recovery, line.two_fa, None, None)
            imported_ids.append(existing.id)
            continue
        account_id = _new_id("acct")
        account = GmailAccount(id=account_id, organization_id=organization_id, group_id=group_id, email=line.email, normalized_email=line.email, lifecycle_status="SETUP_REQUIRED", readiness_status="READY_FOR_LOGIN", recovery_hint=mask_recovery(line.recovery), two_fa_configured=bool(line.two_fa), login_status="NOT_STARTED", gmail_status="NOT_CHECKED", proxy_status="UNASSIGNED", profile_id=profile_folder_name(organization_id, line.email), profile_storage_ref=f"profiles/{organization_id}/{account_id}", profile_status="RESERVED")
        db.add(account)
        db.add(BrowserProfile(id=account.profile_id, organization_id=organization_id, account_id=account_id, storage_reference=account.profile_storage_ref, status="RESERVED", lock_state="UNLOCKED"))
        db.add(AccountSecret(id=_new_id("secret"), account_id=account_id, secret_ref=f"secret://account/{account_id}/primary", encrypted_payload=cipher.encrypt({"password": line.password, "recovery": line.recovery, "2fa": line.two_fa})))
        _add_credential_revision(db, organization_id, account_id, {"password": line.password, "recovery": line.recovery, "2fa": line.two_fa}, account.recovery_hint, account.two_fa_configured, actor_id)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.imported", target_type="gmail_account", target_id=account_id, result="success", metadata_json={"group_id": group_id, "email_domain": line.email.split("@", 1)[1], "source": "bulk", "row_number": line.row_number}))
        imported_ids.append(account_id)
    db.commit()
    if idempotency_key:
        db.add(ImportOperation(id=_new_id("import"), organization_id=organization_id, idempotency_key=idempotency_key, group_id=group_id, imported=len(imported_ids), duplicates=preview["duplicate_rows"], invalid=preview["invalid_rows"], account_ids=imported_ids))
        db.commit()
    return len(imported_ids), preview["duplicate_rows"], preview["invalid_rows"], imported_ids


def account_response(account: GmailAccount) -> dict:
    # Profile authentication is the durable source of truth for a saved
    # account. Older sessions can leave the denormalized login flag at
    # REQUIRED, so reconcile that stale value at the API boundary unless a
    # fresh login session is currently open.
    login_status = account.login_status
    profile = account.browser_profile
    if login_status in {"NOT_STARTED", "REQUIRED"} and profile and profile.authenticated_at and not profile.active_session_id:
        login_status = "AUTHENTICATED"
    profile_status = "SAVED" if profile and profile.authenticated_at and not profile.active_session_id else account.profile_status
    return {"id": account.id, "email": account.email, "group_id": account.group_id, "group_name": account.group.name, "lifecycle_status": account.lifecycle_status, "readiness_status": account.readiness_status, "login_status": login_status, "gmail_status": account.gmail_status, "secret_status": "STORED_ENCRYPTED" if account.secret else "MISSING", "recovery_masked": account.recovery_hint, "two_fa_configured": account.two_fa_configured, "proxy": {"id": account.proxy_id, "label": account.proxy_label, "region": account.proxy_region, "status": account.proxy_status}, "profile": {"id": account.profile_id, "storage_reference": account.profile_storage_ref, "status": profile_status}, "created_at": account.created_at, "updated_at": account.updated_at}


def list_accounts(db: Session, organization_id: str, q: str | None, group_id: str | None, status: str | None, page: int, page_size: int) -> dict:
    filters = [GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED"]
    if q:
        term = f"%{q.strip().lower()}%"
        filters.append(or_(GmailAccount.email.ilike(term), AccountGroup.name.ilike(term)))
    if group_id:
        filters.append(GmailAccount.group_id == group_id)
    if status:
        if status == "AUTHENTICATED":
            filters.append(or_(GmailAccount.login_status == "AUTHENTICATED", (GmailAccount.login_status.in_(["NOT_STARTED", "REQUIRED"]) & (GmailAccount.profile_status == "SAVED"))))
        elif status in {"NOT_STARTED", "REQUIRED", "NONEXISTENT", "VERIFICATION_REQUIRED"}:
            filters.append(GmailAccount.login_status == status)
        else:
            filters.append(GmailAccount.readiness_status == status)
    total = db.scalar(select(func.count(GmailAccount.id)).join(AccountGroup).where(*filters)) or 0
    items = db.scalars(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).join(AccountGroup).where(*filters).order_by(GmailAccount.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": [account_response(item) for item in items], "page": page, "page_size": page_size, "total": total}


def get_account(db: Session, organization_id: str, account_id: str) -> GmailAccount | None:
    return db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))


def update_account_metadata(db: Session, organization_id: str, actor_id: str, account_id: str, proxy_label: str | None, proxy_region: str | None, proxy_value: str | None | object = _UNSET) -> dict:
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot be edited")
    payload: dict = {}
    if account.secret:
        try:
            payload = SecretCipher().decrypt(account.secret.encrypted_payload) or {}
        except Exception as exc:  # noqa: BLE001
            raise ImportValidationError("The encrypted account vault could not be opened") from exc
    if not isinstance(payload, dict):
        payload = {}
    if proxy_value is _UNSET:
        proxy = proxy_from_payload(payload.get("proxy"))
    else:
        try:
            proxy = parse_proxy(proxy_value if isinstance(proxy_value, str) else None)
        except ProxyValidationError as exc:
            raise ImportValidationError(str(exc)) from exc
    account.proxy_id = proxy.proxy_id if proxy else None
    account.proxy_label = proxy_label or (proxy.display_endpoint if proxy else None)
    account.proxy_region = proxy_region
    account.proxy_status = "ASSIGNED" if proxy or proxy_label or proxy_region else "UNASSIGNED"
    if proxy:
        payload["proxy"] = proxy.encrypted_payload()
        _upsert_proxy_inventory(db, organization_id, proxy, account.proxy_label, account.proxy_region)
    elif proxy_value is not _UNSET:
        payload.pop("proxy", None)
    if account.secret:
        account.secret.encrypted_payload = SecretCipher().encrypt(payload)
    else:
        account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", encrypted_payload=SecretCipher().encrypt(payload))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.metadata_updated", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"proxy_label_changed": True, "proxy_region_changed": True, "proxy_configured": bool(proxy), "proxy_authentication": "authenticated" if proxy and proxy.authenticated else "anonymous" if proxy else "none", "proxy_status": account.proxy_status}))
    db.commit()
    return account_response(get_account(db, organization_id, account.id))


def move_account_group(db: Session, organization_id: str, actor_id: str, account_id: str, destination_group_id: str) -> dict:
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot be moved")
    destination = db.scalar(select(AccountGroup).where(AccountGroup.organization_id == organization_id, AccountGroup.id == destination_group_id).with_for_update())
    if not destination:
        raise ImportValidationError("Choose a valid destination group")
    if account.group_id == destination.id:
        raise ImportValidationError("Account is already in this group")
    active_count = db.scalar(select(func.count(GmailAccount.id)).where(GmailAccount.organization_id == organization_id, GmailAccount.group_id == destination.id, GmailAccount.lifecycle_status != "ARCHIVED")) or 0
    if active_count >= destination.capacity:
        raise ImportValidationError("Destination group is at capacity")
    previous_group_id = account.group_id
    account.group_id = destination.id
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.group_moved", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"from_group_id": previous_group_id, "to_group_id": destination.id}))
    db.commit()
    return account_response(get_account(db, organization_id, account.id))


def archive_account(db: Session, organization_id: str, actor_id: str, account_id: str, confirmation_email: str) -> dict:
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.email.lower() != confirmation_email.lower():
        raise ImportValidationError("Confirmation email does not match the account")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Account is already archived")
    now = utcnow()
    active_sessions = db.scalars(select(BrowserSession).where(BrowserSession.organization_id == organization_id, BrowserSession.account_id == account.id, BrowserSession.state.in_(ACTIVE_SESSION_STATES))).all()
    for session in active_sessions:
        close_local_browser(session.id)
        _stop_observer(session.id)
        session.state = "CLOSED"
        session.closed_at = now
        session.last_event_at = now
        session.profile.status = "ARCHIVED"
        session.profile.lock_state = "UNLOCKED"
        session.profile.active_session_id = None
        session.profile.last_closed_at = now
    for item in db.scalars(select(LoginJobItem).where(LoginJobItem.organization_id == organization_id, LoginJobItem.account_id == account.id, LoginJobItem.state.in_(["QUEUED", "RUNNING"]))).all():
        job = db.get(LoginJob, item.job_id)
        if item.state == "RUNNING":
            _release_item_lease(db, item)
            if job:
                _bump_job(db, job, running_count=-1)
        else:
            if job:
                _bump_job(db, job, queued_count=-1)
        item.state = "CANCELLED"
        item.completed_at = now
        item.paused_reason = "Account archived by operator"
        _queue().clear(item.job_id)
        if job and job.queued_count == 0 and job.running_count == 0 and job.status == "RUNNING":
            job.status = "COMPLETED"
            job.completed_at = now
    account.lifecycle_status = "ARCHIVED"
    account.readiness_status = "ARCHIVED"
    account.login_status = "ARCHIVED"
    account.gmail_status = "ARCHIVED"
    if account.browser_profile:
        account.browser_profile.status = "ARCHIVED"
        account.browser_profile.lock_state = "UNLOCKED"
        account.browser_profile.active_session_id = None
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.archived", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"profile_retained": True, "sessions_closed": len(active_sessions), "retention": "policy_managed"}))
    db.commit()
    return account_response(get_account(db, organization_id, account.id))


def _operation_draft_response(draft: OperationDraft) -> dict:
    return {"id": draft.id, "account_id": draft.account_id, "operation_type": draft.operation_type, "version": draft.version, "status": draft.status, "configuration": draft.configuration_json, "created_by": draft.created_by, "created_at": draft.created_at, "updated_at": draft.updated_at}


def create_operation_draft(db: Session, organization_id: str, actor_id: str, account_id: str, operation_type: str, configuration: dict[str, str]) -> dict:
    if operation_type not in OPERATION_TYPES:
        raise ImportValidationError("Choose a supported operation type")
    if len(configuration) > 20:
        raise ImportValidationError("Operation drafts support at most 20 fields")
    safe_configuration: dict[str, str] = {}
    for key, value in configuration.items():
        normalized_key = key.strip().lower()
        if not normalized_key or any(part in normalized_key for part in FORBIDDEN_OPERATION_KEY_PARTS):
            raise ImportValidationError("Operation drafts cannot contain credential or session secrets")
        if len(normalized_key) > 64 or len(value) > 2000:
            raise ImportValidationError("Operation draft fields are too long")
        safe_configuration[normalized_key] = value.strip()
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot receive operation drafts")
    latest = db.scalar(select(func.max(OperationDraft.version)).where(OperationDraft.organization_id == organization_id, OperationDraft.account_id == account_id, OperationDraft.operation_type == operation_type)) or 0
    draft = OperationDraft(id=_new_id("opdraft"), organization_id=organization_id, account_id=account_id, operation_type=operation_type, version=latest + 1, status="DRAFT", configuration_json=safe_configuration, created_by=actor_id)
    db.add(draft)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="operation_draft.created", target_type="operation_draft", target_id=draft.id, result="success", metadata_json={"account_id": account_id, "operation_type": operation_type, "version": draft.version, "configuration_keys": sorted(safe_configuration)}))
    db.commit()
    db.refresh(draft)
    return _operation_draft_response(draft)


def list_operation_drafts(db: Session, organization_id: str, account_id: str) -> list[dict]:
    if not db.scalar(select(GmailAccount.id).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id)):
        raise ImportValidationError("Account not found")
    drafts = db.scalars(select(OperationDraft).where(OperationDraft.organization_id == organization_id, OperationDraft.account_id == account_id).order_by(OperationDraft.created_at.desc())).all()
    return [_operation_draft_response(draft) for draft in drafts]


def update_credentials(db: Session, organization_id: str, actor_id: str, account_id: str, password: str, recovery: str, two_fa: str) -> dict:
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account:
        raise ImportValidationError("Account not found")
    latest = db.scalar(select(func.max(CredentialRevision.version)).where(CredentialRevision.account_id == account.id)) or 0
    for revision in db.scalars(select(CredentialRevision).where(CredentialRevision.account_id == account.id, CredentialRevision.status == "ACTIVE")).all():
        revision.status = "RETIRED"
    payload: dict = {"password": password, "recovery": recovery, "2fa": two_fa}
    if account.secret:
        try:
            existing_payload = SecretCipher().decrypt(account.secret.encrypted_payload) or {}
        except Exception as exc:  # noqa: BLE001
            raise ImportValidationError("The encrypted account vault could not be opened") from exc
        if isinstance(existing_payload, dict) and existing_payload.get("proxy"):
            payload["proxy"] = existing_payload["proxy"]
    encrypted_payload = SecretCipher().encrypt(payload)
    if account.secret:
        account.secret.encrypted_payload = encrypted_payload
        account.secret.key_version = "v1"
    else:
        account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", encrypted_payload=encrypted_payload)
    account.recovery_hint = mask_recovery(recovery)
    account.two_fa_configured = bool(two_fa)
    account.readiness_status = "READY_FOR_LOGIN"
    account.login_status = "REQUIRED"
    if account.browser_profile:
        # Credential rotation invalidates the previous authenticated browser
        # state. The next login must establish a fresh saved profile.
        account.browser_profile.authenticated_at = None
        account.browser_profile.status = "RESERVED"
        account.profile_status = "RESERVED"
    version = latest + 1
    db.add(CredentialRevision(id=_new_id("credrev"), organization_id=organization_id, account_id=account.id, version=version, secret_ref=f"secret://account/{account.id}/v{version}", encrypted_payload=encrypted_payload, recovery_hint=account.recovery_hint, two_fa_configured=account.two_fa_configured, status="ACTIVE", created_by=actor_id))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.credentials_updated", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"version": version, "secret_status": "encrypted", "reauth_required": True}))
    db.commit()
    return account_response(get_account(db, organization_id, account.id))


def credential_revisions(db: Session, organization_id: str, account_id: str) -> list[dict]:
    if not db.scalar(select(GmailAccount.id).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id)):
        raise ImportValidationError("Account not found")
    rows = db.scalars(select(CredentialRevision).where(CredentialRevision.organization_id == organization_id, CredentialRevision.account_id == account_id).order_by(CredentialRevision.version.desc())).all()
    return [{"account_id": row.account_id, "version": row.version, "status": row.status, "secret_status": "STORED_ENCRYPTED", "recovery_masked": row.recovery_hint, "two_fa_configured": row.two_fa_configured, "created_at": row.created_at} for row in rows]


def _login_job_response(job: LoginJob, items: list[LoginJobItem] | None = None) -> dict:
    rows = items if items is not None else list(job.items)
    return {"id": job.id, "group_id": job.group_id, "concurrency_limit": job.concurrency_limit, "status": job.status, "total_count": job.total_count, "queued_count": job.queued_count, "running_count": job.running_count, "succeeded_count": job.succeeded_count, "attention_count": job.attention_count, "failed_count": job.failed_count, "created_at": job.created_at, "started_at": job.started_at, "completed_at": job.completed_at, "updated_at": job.updated_at, "items": [{"id": item.id, "account_id": item.account_id, "account_email": item.account.email, "session_id": item.session_id, "state": item.state, "attempts": item.attempts, "last_error": item.last_error, "updated_at": item.updated_at} for item in rows]}


def create_login_job(db: Session, organization_id: str, actor_id: str, group_id: str | None, account_ids: list[str], concurrency_limit: int) -> dict:
    if group_id and not db.scalar(select(AccountGroup.id).where(AccountGroup.organization_id == organization_id, AccountGroup.id == group_id)):
        raise ImportValidationError("Choose a valid destination group")
    query = select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED", GmailAccount.readiness_status == "READY_FOR_LOGIN")
    if group_id:
        query = query.where(GmailAccount.group_id == group_id)
    if account_ids:
        query = query.where(GmailAccount.id.in_(list(dict.fromkeys(account_ids))))
    accounts = db.scalars(query.order_by(GmailAccount.created_at)).all()
    if not accounts:
        raise ImportValidationError("No accounts are ready for a login batch")
    if len(accounts) > 300:
        raise ImportValidationError("A login batch is limited to 300 accounts")
    job = LoginJob(id=_new_id("loginjob"), organization_id=organization_id, group_id=group_id, concurrency_limit=concurrency_limit, status="QUEUED", total_count=len(accounts), queued_count=len(accounts), created_by=actor_id)
    db.add(job)
    for account in accounts:
        db.add(LoginJobItem(id=_new_id("loginitem"), job_id=job.id, organization_id=organization_id, account_id=account.id, state="QUEUED"))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="login_job.created", target_type="login_job", target_id=job.id, result="success", metadata_json={"group_id": group_id, "account_count": len(accounts), "concurrency_limit": concurrency_limit, "mode": "isolated_profile_queue"}))
    db.commit()
    job = db.scalar(select(LoginJob).options(joinedload(LoginJob.items).joinedload(LoginJobItem.account)).where(LoginJob.id == job.id))
    enqueue_queued_items(db, job.id)
    return _login_job_response(job)


def list_login_jobs(db: Session, organization_id: str, limit: int = 25) -> list[dict]:
    rows = db.scalars(select(LoginJob).where(LoginJob.organization_id == organization_id).order_by(LoginJob.created_at.desc()).limit(limit)).all()
    return [_login_job_response(row, []) for row in rows]


def get_login_job(db: Session, organization_id: str, job_id: str) -> dict | None:
    job = db.scalar(select(LoginJob).options(joinedload(LoginJob.items).joinedload(LoginJobItem.account)).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    return _login_job_response(job) if job else None


def update_login_job_state(db: Session, organization_id: str, actor_id: str, job_id: str, next_state: str) -> dict:
    job = db.scalar(select(LoginJob).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    if not job:
        raise ImportValidationError("Login batch not found")
    transitions = {"QUEUED": {"PAUSED", "CANCELLED", "RUNNING"}, "RUNNING": {"PAUSED", "CANCELLED"}, "PAUSED": {"QUEUED", "CANCELLED", "RUNNING"}}
    if next_state not in transitions.get(job.status, set()):
        raise ImportValidationError(f"Cannot move a {job.status.lower()} batch to {next_state.lower()}")
    job.status = next_state
    logger.info("login job %s -> %s by %s", job.id, next_state, actor_id)
    if next_state == "RUNNING" and not job.started_at:
        job.started_at = utcnow()
    if next_state == "PAUSED":
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="login_job.paused", target_type="login_job", target_id=job.id, result="success", metadata_json={"concurrency_limit": job.concurrency_limit, "running_count": job.running_count}))
        db.commit()
        return get_login_job(db, organization_id, job.id)
    if next_state == "CANCELLED":
        for item in db.scalars(select(LoginJobItem).where(LoginJobItem.job_id == job.id, LoginJobItem.state.in_(["QUEUED", "RUNNING"]))).all():
            if item.session_id:
                session = get_session(db, organization_id, item.session_id)
                if session and session.state in ACTIVE_SESSION_STATES:
                    close_local_browser(session.id)
                    _stop_observer(session.id)
                    now = utcnow()
                    session.state = "CLOSED"
                    session.closed_at = now
                    session.last_event_at = now
                    session.profile.status = "RESERVED"
                    session.profile.lock_state = "UNLOCKED"
                    session.profile.active_session_id = None
                    session.profile.last_closed_at = now
                    _prune_unsaved_profile_folder(session)
                    item.session_id = None
            if item.state == "RUNNING":
                _release_item_lease(db, item)
            item.state = "CANCELLED"
            item.completed_at = utcnow()
            item.paused_reason = "Batch cancelled by operator"
        _queue().clear(job.id)
        db.execute(update(LoginJob).where(LoginJob.id == job.id).values(running_count=0, queued_count=0))
        db.flush()
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action=f"login_job.{next_state.lower()}", target_type="login_job", target_id=job.id, result="success", metadata_json={"concurrency_limit": job.concurrency_limit}))
    db.commit()
    return get_login_job(db, organization_id, job.id)


def _queue() -> RedisQueue:
    return RedisQueue()


def _locks() -> RedisLocks:
    return RedisLocks()


_observers: dict[str, BrowserObserver] = {}


def _start_observer(session_id: str, profile_id: str, organization_id: str, actor_id: str, purpose: str = "login", contacts_csv_path: str | None = None, addon_candidates: list[dict[str, str]] | None = None) -> bool:
    settings = get_settings()
    if not settings.browser_observer_enabled:
        logger.warning("AUTOMATION DISABLED: session %s opened a browser window but BROWSER_OBSERVER_ENABLED is false; automation will not run", session_id)
        _record_observer_status(session_id, organization_id, actor_id, False, "BROWSER_OBSERVER_ENABLED is false")
        return False
    # The observer launches the account's own saved profile as a normal Chrome
    # window through Playwright's persistent context: the command line carries
    # no --remote-debugging-port / --enable-automation flags for Google to see.
    observer = BrowserObserver(
        session_id,
        profile_id,
        0,
        profile_dir=str((Path(settings.profile_root) / profile_id).resolve()),
        executable=resolve_browser_executable(),
        start_url=(
            "https://contacts.google.com/"
            if purpose == "contacts"
            else "https://mail.google.com/mail/u/0/?tab=rm&ogbl#settings/inbox"
            if purpose == "labels_markers"
            else "https://myaccount.google.com/account-enhanced-safe-browsing"
            if purpose == "security"
            else "https://mail.google.com/"
            if purpose == "reuse"
            else "https://mail.google.com/mail/u/0/#inbox"
            if purpose == "addons"
            else None
        ),
        observe_session_page=lambda sid, url, title, text: _observe_in_own_session(organization_id, actor_id, sid, url, title, text),
        resolve_credentials=lambda sid: _resolve_login_credentials(sid),
        resolve_otp_secret=lambda sid: _resolve_login_otp_secret(sid),
        resolve_proxy=lambda sid: _resolve_proxy_options(sid),
        report_event=_report_observer_event,
        on_browser_closed=_on_observer_browser_closed,
        interval=settings.browser_observe_interval,
        settle_seconds=settings.browser_observe_settle_seconds,
        purpose=purpose,
        contacts_csv_path=contacts_csv_path,
        addon_candidates=addon_candidates,
    )
    _observers[session_id] = observer
    observer.start()
    _record_observer_status(session_id, organization_id, actor_id, True)
    logger.info("AUTOMATION ACTIVE: observer launching the isolated browser for session %s", session_id)
    return True


def _record_observer_status(session_id: str, organization_id: str, actor_id: str, attached: bool, reason: str | None = None) -> None:
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session:
            return
        metadata = dict(session.metadata_json or {})
        metadata["observer_attached"] = attached
        if reason:
            metadata["observer_reason"] = reason
        session.metadata_json = metadata
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.observer_attached" if attached else "session.observer_not_attached", target_type="browser_session", target_id=session.id, result="success" if attached else "failure", metadata_json={"account_id": session.account_id, "reason": reason or ""}))
        db.commit()
    finally:
        db.close()


def _stop_observer(session_id: str) -> bool:
    observer = _observers.pop(session_id, None)
    if observer:
        observer.stop()
        return True
    return False


def _on_observer_browser_closed(session_id: str) -> None:
    """Finalize browser-close bookkeeping after Playwright releases the
    persistent profile directory.

    Unsaved profile cleanup is intentionally deferred until this callback so
    the closing Chromium process cannot recreate the scratch directory after
    the service removes it.
    """
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session:
            return
        metadata = dict(session.metadata_json or {})
        metadata["browser_opened"] = False
        metadata["observer_attached"] = False
        session.metadata_json = metadata
        if session.state == "CLOSED" and session.login_state != "AUTHENTICATED":
            _prune_unsaved_profile_folder(session)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="session.browser_closed", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": session.account_id, "profile_id": session.profile_id}))
        db.commit()
    except Exception:  # noqa: BLE001 - observer shutdown must remain best effort
        db.rollback()
        logger.exception("browser-close bookkeeping failed for session %s", session_id)
    finally:
        db.close()


def _resolve_login_credentials(session_id: str) -> tuple[str, str] | None:
    """Return (identifier, password) decrypted from the account vault for the
    session's account, or None when auto-login is disabled or nothing is stored.
    The observer types these only into Google's own sign-in host."""
    if not get_settings().browser_auto_login:
        return None
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session or not session.account or not session.account.secret:
            return None
        payload = SecretCipher().decrypt(session.account.secret.encrypted_payload)
        password = (payload or {}).get("password", "")
        identifier = (session.account.email or "").strip().lower()
        if not identifier or not password:
            return None
        return identifier, password
    except Exception:  # noqa: BLE001 - never break a session over missing secrets
        logger.exception("could not resolve login credentials for session %s", session_id)
        return None
    finally:
        db.close()


def _resolve_login_otp_secret(session_id: str) -> str | None:
    """Return the account's stored Authenticator (TOTP) secret so the observer
    can generate one-time codes, or None when auto-login is off or nothing is
    stored. The secret is typed by the observer into Google's own code field."""
    if not get_settings().browser_auto_login:
        return None
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session or not session.account or not session.account.secret:
            return None
        payload = SecretCipher().decrypt(session.account.secret.encrypted_payload)
        secret = (payload or {}).get("2fa", "").strip()
        return secret or None
    except Exception:  # noqa: BLE001 - never break a session over missing secrets
        logger.exception("could not resolve OTP secret for session %s", session_id)
        return None
    finally:
        db.close()


def _resolve_proxy_options(session_id: str) -> dict[str, str] | None:
    """Decrypt an account proxy only for the isolated browser launch.

    An assigned proxy is a hard network boundary. If its encrypted material
    cannot be read, do not silently fall back to the machine's direct route.
    The observer treats the raised error as a failed launch and closes the
    session without opening a misleading blank/direct browser.
    """
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session or not session.account or not session.account.secret:
            if session and session.account and session.account.proxy_id:
                raise ImportValidationError("The assigned proxy connection is unavailable")
            return None
        payload = SecretCipher().decrypt(session.account.secret.encrypted_payload) or {}
        proxy = proxy_from_payload((payload or {}).get("proxy")) if isinstance(payload, dict) else None
        if session.account.proxy_id and not proxy:
            raise ImportValidationError("The assigned proxy connection is unavailable")
        return proxy.playwright_options() if proxy else None
    except Exception:  # noqa: BLE001 - preserve the hard proxy boundary
        logger.exception("could not resolve proxy options for session %s", session_id)
        raise
    finally:
        db.close()


def _account_has_totp_secret(db: Session, session: BrowserSession) -> bool:
    """Whether the session's account vault holds a TOTP secret, meaning the
    observer can generate codes instead of pausing for the operator."""
    try:
        if not session.account or not session.account.secret:
            return False
        payload = SecretCipher().decrypt(session.account.secret.encrypted_payload)
        return bool((payload or {}).get("2fa", "").strip())
    except Exception:  # noqa: BLE001
        return False


def _report_observer_event(session_id: str, event: str, detail: str = "", result: str = "success") -> None:
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session:
            return
        terminal_events = {"login.failed", "reuse.failed", "contacts.failed", "contacts.imported", "labels_markers.failed", "labels_markers.completed", "security.failed", "security.completed", "addons.failed", "addons.completed"}
        if event == "addons.permission_required" and session.state in ACTIVE_SESSION_STATES:
            session.state = "NEEDS_ATTENTION"
            session.login_state = "VERIFICATION_REQUIRED"
            session.failure_reason = detail[:300] or "Marketplace permission or billing approval is required"
            session.last_event_at = utcnow()
        if event in terminal_events and session.state in ACTIVE_SESSION_STATES:
            # The observer owns the browser-close operation and will release
            # the process in its finally block. Finalize the durable session
            # here so a timeout cannot leave the UI showing a dead RUNNING
            # session or hold the profile lock indefinitely.
            now = utcnow()
            session.state = "CLOSED"
            if event == "contacts.imported":
                session.login_state = "CONTACTS_IMPORTED"
            elif event == "labels_markers.completed":
                session.login_state = "LABELS_MARKERS_COMPLETED"
            elif event == "security.completed":
                session.login_state = "SECURITY_COMPLETED"
            elif event == "addons.completed":
                session.login_state = "ADDONS_COMPLETED"
            elif event == "security.failed":
                session.login_state = "SECURITY_FAILED"
            elif event == "addons.failed":
                session.login_state = "ADDONS_FAILED"
            elif event == "login.failed":
                session.login_state = "FAILED"
            elif event == "reuse.failed":
                session.login_state = "FAILED"
            session.closed_at = now
            session.last_event_at = now
            session.failure_reason = None if event in {"contacts.imported", "labels_markers.completed", "security.completed", "addons.completed"} else (detail[:300] or "Operation failed before completion")
            if event in {"contacts.imported", "labels_markers.completed", "security.completed", "addons.completed"}:
                session.account.login_status = "AUTHENTICATED"
                session.account.profile_status = "SAVED"
            elif event == "login.failed" and session.profile.authenticated_at is None:
                session.account.login_status = "NOT_STARTED"
                session.account.profile_status = "RESERVED"
            session.profile.status = "RESERVED"
            session.profile.lock_state = "UNLOCKED"
            session.profile.active_session_id = None
            session.profile.last_closed_at = now
            metadata = dict(session.metadata_json or {})
            metadata["browser_opened"] = False
            metadata["terminal_result"] = {
                "contacts.imported": "CONTACTS_IMPORTED",
                "contacts.failed": "CONTACTS_IMPORT_FAILED",
                "labels_markers.completed": "LABELS_MARKERS_COMPLETED",
                "labels_markers.failed": "LABELS_MARKERS_FAILED",
                "security.completed": "SECURITY_COMPLETED",
                "security.failed": "SECURITY_FAILED",
                "addons.completed": "ADDONS_COMPLETED",
                "addons.failed": "ADDONS_FAILED",
                "login.failed": "LOGIN_FAILED",
                "reuse.failed": "REOPEN_FAILED",
            }[event]
            session.metadata_json = metadata
        db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action=f"session.auto_login.{event}", target_type="browser_session", target_id=session.id, result=result, metadata_json={"detail": detail[:300], "account_id": session.account_id}))
        if event in terminal_events:
            terminal_result = {
                "contacts.imported": "CONTACTS_IMPORTED",
                "contacts.failed": "CONTACTS_IMPORT_FAILED",
                "labels_markers.completed": "LABELS_MARKERS_COMPLETED",
                "labels_markers.failed": "LABELS_MARKERS_FAILED",
                "security.completed": "SECURITY_COMPLETED",
                "security.failed": "SECURITY_FAILED",
                "addons.completed": "ADDONS_COMPLETED",
                "addons.failed": "ADDONS_FAILED",
                "login.failed": "LOGIN_FAILED",
                "reuse.failed": "REOPEN_FAILED",
            }[event]
            db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="session.closed", target_type="browser_session", target_id=session.id, result="success" if event in {"contacts.imported", "labels_markers.completed", "security.completed", "addons.completed"} else "failure", metadata_json={"account_id": session.account_id, "reason": detail[:300], "terminal_result": terminal_result}))
        db.commit()
        logger.info("session %s: auto-login %s %s", session_id, event, result)
    finally:
        db.close()


def _prune_unsaved_profile_folder(session: BrowserSession) -> None:
    """Discard the on-disk profile unless the account is fully connected.

    A profile dir is only persisted once Google confirms the sign-in. Sessions
    that close before authentication remove their scratch user-data dir so no
    profile lingers for accounts that were never connected (previously saved
    profiles are always kept).
    """
    if session.profile is not None and session.profile.authenticated_at is not None:
        return
    if session.login_state == "AUTHENTICATED":
        return
    remove_local_profile(session.profile_id)


def cleanup_unsaved_invalid_profiles(db: Session, organization_id: str) -> int:
    """Remove orphaned on-disk profile directories for accounts already
    classified as nonexistent.

    This is intentionally narrow: authenticated profiles and accounts with any
    other status are never touched. It repairs scratch directories left by
    older observer versions on the next safe application startup.
    """
    rows = db.scalars(
        select(BrowserProfile)
        .join(GmailAccount, GmailAccount.id == BrowserProfile.account_id)
        .where(
            BrowserProfile.organization_id == organization_id,
            BrowserProfile.authenticated_at.is_(None),
            GmailAccount.login_status == "NONEXISTENT",
        )
    ).all()
    for profile in rows:
        remove_local_profile(profile.id)
    if rows:
        logger.info("startup cleanup removed %s unsaved nonexistent-account profile director%s", len(rows), "y" if len(rows) == 1 else "ies")
    return len(rows)


def recover_orphaned_sessions(db: Session, organization_id: str, actor_id: str = "system") -> int:
    """Close active session rows whose dedicated browser is already gone.

    A process restart cannot run the old observer's close callback. Recovering
    these rows during startup prevents a dead contacts/login session from
    holding the account profile lock or appearing as RUNNING in the UI.
    """
    rows = db.scalars(
        select(BrowserSession)
        .options(joinedload(BrowserSession.account), joinedload(BrowserSession.profile))
        .where(BrowserSession.organization_id == organization_id, BrowserSession.state.in_(ACTIVE_SESSION_STATES))
    ).unique().all()
    recovered = 0
    for session in rows:
        browser_opened = bool((session.metadata_json or {}).get("browser_opened"))
        if browser_opened and local_browser_is_alive(session.profile_id, session.id):
            continue
        _recover_stale_session(db, organization_id, actor_id, session)
        recovered += 1
    if recovered:
        db.commit()
        logger.info("startup recovered %s orphaned active session%s", recovered, "" if recovered == 1 else "s")
    return recovered


def _recover_stale_session(db: Session, organization_id: str, actor_id: str, session: BrowserSession) -> None:
    """Release a durable session row whose dedicated browser is gone."""
    close_local_browser(session.id)
    observer_stopped = _stop_observer(session.id)
    now = utcnow()
    session.state = "CLOSED"
    session.closed_at = now
    session.last_event_at = now
    session.profile.status = "RESERVED"
    session.profile.lock_state = "UNLOCKED"
    session.profile.active_session_id = None
    session.profile.last_closed_at = now
    if session.login_state == "REQUIRED" and session.profile.authenticated_at is None:
        session.account.login_status = "NOT_STARTED"
        session.account.profile_status = "RESERVED"
    elif session.profile.authenticated_at is not None:
        session.account.login_status = "AUTHENTICATED"
        session.account.profile_status = "SAVED"
    if not observer_stopped:
        _prune_unsaved_profile_folder(session)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.stale_recovered", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": session.account_id, "profile_id": session.profile_id, "reason": "dedicated_browser_not_running"}))
    logger.warning("session %s: recovered stale profile lock because its browser is not running", session.id)


def _observe_in_own_session(organization_id: str, actor_id: str, session_id: str, url: str, title: str, text: str) -> None:
    db = SessionLocal()
    try:
        observe_session_page(db, organization_id, actor_id, session_id, url, title, text)
    finally:
        db.close()


def enqueue_queued_items(db: Session, job_id: str) -> int:
    """Push every QUEUED item of a job into the Redis queue."""
    items = db.scalars(select(LoginJobItem).where(LoginJobItem.job_id == job_id, LoginJobItem.state == "QUEUED")).all()
    if not items:
        return 0
    entries = [(item.id, item.account_id, item.created_at.timestamp()) for item in items]
    _queue().enqueue_many(job_id, entries)
    return len(entries)


def dispatch_ready_items(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    """Start as many queued items as the job's concurrency and the global
    active-profile cap allow. Each start acquires one account/profile lease."""
    job = db.scalar(select(LoginJob).options(joinedload(LoginJob.items).joinedload(LoginJobItem.account)).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    if not job:
        raise ImportValidationError("Login batch not found")
    if job.status != "RUNNING":
        raise ImportValidationError("Start the login batch before dispatching profiles")
    settings = get_settings()
    queue = _queue()
    locks = _locks()
    worker_id = _worker_token() if actor_id.startswith(WORKER_PREFIX) else API_WORKER_ID
    started = 0
    while True:
        running = queue.running_count(job.id)
        if running >= job.concurrency_limit:
            break
        if queue.active_profile_count() >= settings.worker_max_active_profiles:
            break
        if job.queued_count <= 0:
            break
        candidate = queue.pop_next(job.id)
        if not candidate:
            break
        item = db.get(LoginJobItem, candidate["item_id"])
        if not item or item.state != "QUEUED" or item.job_id != job.id:
            continue
        token = locks.acquire(item.account_id, worker_id, "", settings.worker_lock_ttl * 1000)
        if not token:
            queue.requeue(job.id, item.id, item.account_id)
            break
        try:
            session = start_login_session(db, organization_id, actor_id, item.account_id)
        except BrowserLaunchError as exc:
            # Launch failed before anything was persisted: leave the item queued.
            # Break, not continue, so the worker does not hot-loop the same item.
            locks.force_release(item.account_id)
            queue.requeue(job.id, item.id, item.account_id)
            logger.warning("login item %s (account=%s) not dispatched; requeued: %s", item.id, item.account_id, exc)
            break
        except ImportValidationError as exc:
            locks.force_release(item.account_id)
            item.state = "ATTENTION"
            item.last_error = "Account could not be opened for this batch"
            item.paused_reason = str(exc)[:500]
            item.completed_at = utcnow()
            _bump_job(db, job, queued_count=-1, attention_count=1)
            db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="login_job.item_attention", target_type="login_job_item", target_id=item.id, result="attention", metadata_json={"job_id": job.id, "account_id": item.account_id, "reason": "profile_unavailable"}))
            continue
        locks.bind_session(item.account_id, token, session.id)
        now = utcnow()
        item.session_id = session.id
        item.state = "RUNNING"
        item.attempts += 1
        item.worker_id = worker_id
        item.heartbeat_at = now
        item.lock_key = token
        item.last_error = None
        item.paused_reason = None
        item.completed_at = None
        _bump_job(db, job, queued_count=-1, running_count=1)
        db.add(JobLock(id=_new_id("joblock"), organization_id=organization_id, account_id=item.account_id, profile_id=session.profile_id, session_id=session.id, worker_id=worker_id, lock_key=token, acquired_at=now, heartbeat_at=now))
        queue.add_active_profile(item.account_id)
        queue.add_running(job.id, item.account_id)
        started += 1
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="login_job.dispatched", target_type="login_job", target_id=job.id, result="success", metadata_json={"opened_count": started, "concurrency_limit": job.concurrency_limit}))
    db.commit()
    logger.info("login job %s: dispatched %s session(s) (concurrency_limit=%s, queued_left=%s, running=%s)", job.id, started, job.concurrency_limit, job.queued_count, job.running_count)
    return get_login_job(db, organization_id, job.id)


def dispatch_login_job(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    job = db.scalar(select(LoginJob).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    if not job:
        raise ImportValidationError("Login batch not found")
    if job.status != "RUNNING":
        raise ImportValidationError("Start the login batch before dispatching profiles")
    enqueue_queued_items(db, job_id)
    return dispatch_ready_items(db, organization_id, actor_id, job_id)


def _release_item_lease(db: Session, item: LoginJobItem) -> None:
    """Drop the Redis lock, active-profile set, job running set, and mark the
    durable job_locks row released."""
    locks = _locks()
    queue = _queue()
    if item.lock_key:
        locks.release(item.account_id, item.lock_key)
    queue.remove_active_profile(item.account_id)
    queue.remove_running(item.job_id, item.account_id)
    if item.session_id:
        lock_row = db.scalar(select(JobLock).where(JobLock.released == False, JobLock.account_id == item.account_id, JobLock.session_id == item.session_id))
        if lock_row:
            lock_row.released = True
            lock_row.released_at = utcnow()
            lock_row.reason = "session_finalized"
            lock_row.heartbeat_at = None
    item.lock_key = None


def _finalize_item(db: Session, item: LoginJobItem, new_state: str, error: str | None = None) -> LoginJob | None:
    """Transition a RUNNING item to a terminal/attention state and free its lease."""
    job = db.get(LoginJob, item.job_id)
    _release_item_lease(db, item)
    item.state = new_state
    item.last_error = (error or "")[:500] or None
    item.heartbeat_at = None
    item.worker_id = None
    item.completed_at = utcnow()
    if new_state == "SUCCEEDED":
        item.paused_reason = None
    if job:
        deltas = {"running_count": -1}
        if new_state == "SUCCEEDED":
            deltas["succeeded_count"] = 1
        elif new_state == "ATTENTION":
            deltas["attention_count"] = 1
        elif new_state == "FAILED":
            deltas["failed_count"] = 1
        _bump_job(db, job, **deltas)
        if job.queued_count == 0 and job.running_count == 0:
            job.status = "COMPLETED"
            job.completed_at = utcnow()
    logger.info("login item %s -> %s (account=%s, job=%s)", item.id, new_state, item.account_id, item.job_id)
    return job


def _maybe_dispatch_next(db: Session, job: LoginJob | None) -> None:
    """After a session releases a slot, the background worker immediately
    starts the next queued account. When the worker is disabled, dispatch
    stays manual so operators control the next wave via the API."""
    if not get_settings().worker_enabled:
        return
    if not job or job.status != "RUNNING":
        return
    if job.queued_count <= 0 or job.running_count >= job.concurrency_limit:
        return
    try:
        enqueue_queued_items(db, job.id)
        dispatch_ready_items(db, job.organization_id, API_WORKER_ID, job.id)
    except ImportValidationError:
        db.rollback()


def _sync_job_item_for_session(db: Session, session_id: str, session_state: str) -> LoginJob | None:
    item = db.scalar(select(LoginJobItem).where(LoginJobItem.session_id == session_id))
    if not item or item.state != "RUNNING":
        return None
    job = db.get(LoginJob, item.job_id)
    session = db.get(BrowserSession, session_id)
    # Pause the affected account whenever Google posed a challenge or the
    # worker hit an unknown page; the profile stays reserved for a human.
    if session_state == "RUNNING":
        final_item_state = "SUCCEEDED"
        error = None
    elif session_state == "NEEDS_ATTENTION":
        item.paused_reason = "Challenge or unknown page raised for operator attention"
        return _finalize_item(db, item, "ATTENTION")
    elif session_state == "CLOSED":
        if session is not None and session.login_state == "ACCOUNT_NOT_FOUND":
            item.paused_reason = "Google confirmed that this account does not exist"
            return _finalize_item(db, item, "ATTENTION", error=session.failure_reason)
        _release_item_lease(db, item)
        item.state = "QUEUED"
        item.session_id = None
        item.worker_id = None
        item.heartbeat_at = None
        item.completed_at = None
        item.paused_reason = "Session closed before authentication; queued for retry"
        if job:
            _bump_job(db, job, running_count=-1, queued_count=1)
            job.status = "PAUSED"
        _queue().requeue(item.job_id, item.id, item.account_id)
        return job
    else:
        final_item_state = "ATTENTION"
        error = "Session closed before authentication"
    return _finalize_item(db, item, final_item_state, error=error)


ACTIVE_SESSION_STATES = {"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}


def _ensure_browser_profile(db: Session, account: GmailAccount) -> BrowserProfile:
    profile = db.scalar(select(BrowserProfile).where(BrowserProfile.account_id == account.id))
    if profile:
        return profile
    profile = BrowserProfile(
        id=account.profile_id or _new_id("profile"),
        organization_id=account.organization_id,
        account_id=account.id,
        storage_reference=account.profile_storage_ref or f"profiles/{account.organization_id}/{account.id}",
        status="RESERVED",
        lock_state="UNLOCKED",
    )
    db.add(profile)
    db.flush()
    return profile


def _duration_seconds(start, end) -> int | None:
    if not start or not end:
        return None
    # SQLite may return legacy DateTime values as naive while newer rows are
    # timezone-aware. Normalize both to UTC-naive for arithmetic only; the
    # original values are still returned unchanged through the API.
    def normalize(value):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    return max(0, round((normalize(end) - normalize(start)).total_seconds()))


def session_response(session: BrowserSession) -> dict:
    metadata = session.metadata_json or {}
    purpose = metadata.get("purpose", "login")
    operation_status, status_detail = _session_operation_status(session)
    end_at = session.closed_at or session.last_event_at
    duration_seconds = _duration_seconds(session.opened_at, end_at)
    return {
        "id": session.id,
        "account_id": session.account_id,
        "account_email": session.account.email,
        "profile_id": session.profile_id,
        "profile_storage_reference": session.profile.storage_reference,
        "profile_status": session.profile.status,
        "profile_saved": bool(session.profile.authenticated_at),
        "profile_authenticated_at": session.profile.authenticated_at,
        "state": session.state,
        "login_state": session.login_state,
        "purpose": purpose,
        "action": {"login": "Login", "reuse": "Reopen profile", "contacts": "Import contacts", "security": "Browser security", "labels_markers": "Labels & Markers", "addons": "Install Gmail add-ons"}.get(purpose, purpose.replace("_", " ").title()),
        "operation_status": operation_status,
        "status_detail": status_detail,
        "duration_seconds": duration_seconds,
        "runtime_type": session.runtime_type,
        "browser_opened": bool(session.metadata_json.get("browser_opened", False)),
        "observer_attached": bool(session.metadata_json.get("observer_attached", False)),
        "automation_hint": _session_automation_hint(session),
        "session_url": f"/session/{session.id}",
        "opened_at": session.opened_at,
        "last_event_at": session.last_event_at,
        "closed_at": session.closed_at,
        "failure_reason": session.failure_reason,
    }


def _session_operation_status(session: BrowserSession) -> tuple[str, str | None]:
    """Derive an operator-facing outcome from durable session evidence."""
    successful_states = {"AUTHENTICATED", "CONTACTS_IMPORTED", "LABELS_MARKERS_COMPLETED", "SECURITY_COMPLETED", "ADDONS_COMPLETED"}
    failed_states = {"ACCOUNT_NOT_FOUND", "FAILED", "LAUNCH_FAILED", "SECURITY_FAILED", "ADDONS_FAILED"}
    interrupted_states = {"VERIFICATION_REQUIRED", "SECURITY_CHALLENGE", "CAPTCHA", "MFA_REQUIRED"}
    purpose = (session.metadata_json or {}).get("purpose", "login")
    if purpose in {"contacts", "labels_markers", "security", "addons"} and session.login_state == "AUTHENTICATED":
        return "NOT_FINISHED", "The profile is authenticated, but this operation has not reached its verified completion event."
    if session.login_state in successful_states:
        return "COMPLETED", "The operation reached its recorded completion state."
    if session.login_state in failed_states:
        return "FAILED", session.failure_reason or "The account or operation was not completed."
    if session.state == "NEEDS_ATTENTION" or session.login_state in interrupted_states:
        return "INTERRUPTED", session.failure_reason or "The operation needs attention before it can continue."
    if session.state in {"STARTING", "LOGIN_REQUIRED", "RUNNING"}:
        return "IN_PROGRESS", "The isolated browser operation is still in progress."
    return "NOT_FINISHED", session.failure_reason or "The session closed before the operation was completed."


def _session_automation_hint(session: BrowserSession) -> str | None:
    """Explain why the opened browser is or is not being signed in
    automatically, so the UI never leaves the operator guessing."""
    settings = get_settings()
    if not settings.browser_launch_enabled:
        return None
    if not settings.browser_observer_enabled:
        return "Launcher and observer are both required for automation (BROWSER_LAUNCH_ENABLED and BROWSER_OBSERVER_ENABLED must be true)."
    if not bool(session.metadata_json.get("observer_attached")):
        return "Browser open but the observer could not attach; check that playwright is installed and BROWSER_OBSERVER_ENABLED=true."
    if not settings.browser_auto_login:
        return "Auto sign-in is disabled (BROWSER_AUTO_LOGIN=false); sign in manually in the opened browser."
    secret = session.account.secret if session.account else None
    if secret is not None:
        try:
            payload = SecretCipher().decrypt(secret.encrypted_payload)
        except Exception:  # noqa: BLE001
            payload = {}
    else:
        payload = {}
    if not (payload or {}).get("password"):
        return "No stored password for this account; add it under Update credentials to enable auto sign-in."
    return "Auto sign-in is running in the opened browser."


def get_session(db: Session, organization_id: str, session_id: str) -> BrowserSession | None:
    return db.scalar(select(BrowserSession).options(joinedload(BrowserSession.account), joinedload(BrowserSession.profile)).where(BrowserSession.organization_id == organization_id, BrowserSession.id == session_id))


def list_sessions(db: Session, organization_id: str, account_id: str | None, limit: int = 25, group_id: str | None = None, latest_per_account: bool = False, purpose: str | None = None, outcome: str | None = None) -> list[dict]:
    query = select(BrowserSession).options(joinedload(BrowserSession.account), joinedload(BrowserSession.profile)).where(BrowserSession.organization_id == organization_id).order_by(BrowserSession.created_at.desc()).limit(limit)
    if account_id:
        query = query.where(BrowserSession.account_id == account_id)
    if group_id:
        query = query.join(BrowserSession.account).where(GmailAccount.group_id == group_id)
    if latest_per_account:
        ranked = select(BrowserSession.id, func.row_number().over(partition_by=BrowserSession.account_id, order_by=(BrowserSession.created_at.desc(), BrowserSession.id.desc())).label("session_rank")).where(BrowserSession.organization_id == organization_id).subquery()
        query = query.join(ranked, BrowserSession.id == ranked.c.id).where(ranked.c.session_rank == 1)
    if purpose:
        query = query.where(BrowserSession.metadata_json["purpose"].as_string() == purpose)
    rows = [session_response(item) for item in db.scalars(query).unique().all()]
    if outcome:
        rows = [row for row in rows if row["operation_status"] == outcome]
    return rows


def browser_profile_response(profile: BrowserProfile) -> dict:
    """Return safe profile inventory metadata; never expose browser contents."""
    profile_status = "SAVED" if profile.authenticated_at and not profile.active_session_id else profile.status
    return {
        "id": profile.id,
        "account_id": profile.account_id,
        "account_email": profile.account.email,
        "storage_reference": profile.storage_reference,
        "status": profile_status,
        "lock_state": profile.lock_state,
        "active_session_id": profile.active_session_id,
        "last_opened_at": profile.last_opened_at,
        "last_closed_at": profile.last_closed_at,
        "authenticated_at": profile.authenticated_at,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def list_browser_profiles(db: Session, organization_id: str, query_text: str | None = None, status_filter: str | None = None, limit: int = 100) -> list[dict]:
    query = (
        select(BrowserProfile)
        .join(BrowserProfile.account)
        .options(joinedload(BrowserProfile.account))
        .where(BrowserProfile.organization_id == organization_id)
        .order_by(BrowserProfile.updated_at.desc())
        .limit(limit)
    )
    if query_text:
        term = f"%{query_text.strip().lower()}%"
        query = query.where(or_(func.lower(GmailAccount.email).like(term), BrowserProfile.id.ilike(term), BrowserProfile.storage_reference.ilike(term)))
    if status_filter:
        if status_filter == "SAVED":
            query = query.where(or_(BrowserProfile.status == "SAVED", (BrowserProfile.authenticated_at.is_not(None) & BrowserProfile.active_session_id.is_(None))))
        else:
            query = query.where(BrowserProfile.status == status_filter)
    # A database row can be a reservation created during account onboarding.
    # The inventory page is intentionally a physical-profile inventory: do not
    # expose reservations as profiles until the persistent user-data directory
    # actually exists on the configured browser host.
    profile_root = Path(get_settings().profile_root).resolve()
    real_profiles = []
    for item in db.scalars(query).unique().all():
        profile_path = (profile_root / item.id).resolve()
        if profile_path.is_relative_to(profile_root) and profile_path.is_dir():
            real_profiles.append(browser_profile_response(item))
    return real_profiles


def _safe_secret_payload(account: GmailAccount) -> dict:
    if not account.secret:
        return {}
    try:
        payload = SecretCipher().decrypt(account.secret.encrypted_payload)
    except Exception as exc:  # noqa: BLE001
        raise ImportValidationError("The encrypted account vault could not be opened") from exc
    return payload if isinstance(payload, dict) else {}


def _proxy_inventory_item(observation: ProxyInventory | None) -> dict:
    if not observation:
        return {"provider": None, "country": None, "city": None, "public_ip": None, "test_status": "NOT_TESTED", "test_error": None, "latency_ms": None, "last_tested_at": None}
    return {"provider": observation.provider, "country": observation.country, "city": observation.city, "public_ip": observation.public_ip, "test_status": observation.test_status, "test_error": observation.test_error, "latency_ms": observation.latency_ms, "last_tested_at": observation.last_tested_at}


def list_proxies(db: Session, organization_id: str, query_text: str | None = None, status_filter: str | None = None, group_id: str | None = None) -> list[dict]:
    """Build a safe proxy inventory, including currently unassigned records."""
    account_query = select(GmailAccount).options(joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED").order_by(GmailAccount.email)
    if group_id:
        account_query = account_query.where(GmailAccount.group_id == group_id)
    accounts = db.scalars(account_query).all()
    observations = {item.id: item for item in db.scalars(select(ProxyInventory).where(ProxyInventory.organization_id == organization_id)).all()}
    grouped: dict[str, dict] = {}
    for observation in observations.values():
        grouped[observation.id] = {
            "id": observation.id,
            "label": observation.label,
            "region": observation.region,
            "status": "UNASSIGNED",
            "account_count": 0,
            "authenticated_count": 0,
            "account_emails": [],
            "account_refs": [],
            **_proxy_inventory_item(observation),
        }
    for account in accounts:
        if not account.proxy_id:
            continue
        item = grouped.setdefault(account.proxy_id, {"id": account.proxy_id, "label": account.proxy_label, "region": account.proxy_region, "status": account.proxy_status, "account_count": 0, "authenticated_count": 0, "account_emails": [], "account_refs": [], **_proxy_inventory_item(observations.get(account.proxy_id))})
        item["status"] = account.proxy_status
        item["account_count"] += 1
        item["authenticated_count"] += int(account.login_status == "AUTHENTICATED" or bool(account.browser_profile and account.browser_profile.authenticated_at))
        item["account_emails"].append(account.email)
        item["account_refs"].append({"id": account.id, "email": account.email})
        if item["label"] is None and account.proxy_label:
            item["label"] = account.proxy_label
        if item["region"] is None and account.proxy_region:
            item["region"] = account.proxy_region
    if query_text:
        needle = query_text.strip().lower()
        grouped = {key: item for key, item in grouped.items() if needle in " ".join([str(item.get("id") or ""), str(item.get("label") or ""), str(item.get("region") or ""), str(item.get("provider") or ""), str(item.get("country") or ""), *item["account_emails"]]).lower()}
    if status_filter:
        grouped = {key: item for key, item in grouped.items() if item["status"] == status_filter or item["test_status"] == status_filter}
    return list(grouped.values())


def _proxy_account(db: Session, organization_id: str, proxy_id: str) -> GmailAccount | None:
    return db.scalar(select(GmailAccount).options(joinedload(GmailAccount.secret)).where(GmailAccount.organization_id == organization_id, GmailAccount.proxy_id == proxy_id, GmailAccount.lifecycle_status != "ARCHIVED").order_by(GmailAccount.email))


def _proxy_inventory_record(db: Session, organization_id: str, proxy_id: str) -> ProxyInventory | None:
    return db.scalar(select(ProxyInventory).where(ProxyInventory.organization_id == organization_id, ProxyInventory.id == proxy_id))


def _upsert_proxy_inventory(db: Session, organization_id: str, proxy, label: str | None = None, region: str | None = None) -> ProxyInventory:
    observation = _proxy_inventory_record(db, organization_id, proxy.proxy_id)
    if observation is None:
        observation = ProxyInventory(id=proxy.proxy_id, organization_id=organization_id)
        db.add(observation)
    observation.encrypted_payload = SecretCipher().encrypt(proxy.encrypted_payload())
    if label is not None:
        observation.label = label
    elif observation.label is None:
        observation.label = proxy.display_endpoint
    if region is not None:
        observation.region = region
    return observation


def _proxy_config_for_id(db: Session, organization_id: str, proxy_id: str):
    observation = _proxy_inventory_record(db, organization_id, proxy_id)
    if observation and observation.encrypted_payload:
        try:
            payload = SecretCipher().decrypt(observation.encrypted_payload)
        except Exception as exc:  # noqa: BLE001
            raise ImportValidationError("The encrypted proxy connection could not be opened") from exc
        proxy = proxy_from_payload(payload)
        if proxy:
            return proxy, None
    account = _proxy_account(db, organization_id, proxy_id)
    if not account:
        raise ImportValidationError("Proxy assignment not found")
    proxy = proxy_from_payload(_safe_secret_payload(account).get("proxy"))
    if not proxy:
        raise ImportValidationError("The proxy connection is not available for this assignment")
    return proxy, account


def assign_proxy_accounts(db: Session, organization_id: str, actor_id: str, account_ids: list[str], proxy_id: str | None = None, proxy_value: str | None = None, label: str | None | object = _UNSET, region: str | None | object = _UNSET) -> dict:
    """Assign one existing or newly entered proxy to many accounts."""
    if not account_ids:
        raise ImportValidationError("Choose at least one account")
    try:
        proxy = parse_proxy(proxy_value) if proxy_value is not None and proxy_value.strip() else None
    except ProxyValidationError as exc:
        raise ImportValidationError(str(exc)) from exc
    source_account = None
    if proxy is None:
        if not proxy_id:
            raise ImportValidationError("Enter a proxy connection or choose an existing proxy")
        proxy, source_account = _proxy_config_for_id(db, organization_id, proxy_id)
    observation = _upsert_proxy_inventory(
        db,
        organization_id,
        proxy,
        (label.strip() if isinstance(label, str) and label.strip() else None) if label is not _UNSET else None,
        (region.strip() if isinstance(region, str) and region.strip() else None) if region is not _UNSET else None,
    )
    accounts = db.scalars(select(GmailAccount).options(joinedload(GmailAccount.secret)).where(GmailAccount.organization_id == organization_id, GmailAccount.id.in_(account_ids), GmailAccount.lifecycle_status != "ARCHIVED").with_for_update()).all()
    if len(accounts) != len(set(account_ids)):
        raise ImportValidationError("One or more selected accounts could not be found")
    safe_label = (label.strip() if label and label.strip() else None) if label is not _UNSET else (source_account.proxy_label if source_account else observation.label or proxy.display_endpoint)
    safe_region = (region.strip() if region and region.strip() else None) if region is not _UNSET else (source_account.proxy_region if source_account else observation.region)
    observation.label = safe_label
    observation.region = safe_region
    for account in accounts:
        payload = _safe_secret_payload(account)
        payload["proxy"] = proxy.encrypted_payload()
        if account.secret:
            account.secret.encrypted_payload = SecretCipher().encrypt(payload)
        else:
            account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", encrypted_payload=SecretCipher().encrypt(payload))
        account.proxy_id = proxy.proxy_id
        account.proxy_label = safe_label
        account.proxy_region = safe_region
        account.proxy_status = "ASSIGNED"
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="proxy.assigned", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"proxy_id": proxy.proxy_id, "proxy_authentication": "authenticated" if proxy.authenticated else "anonymous"}))
    db.commit()
    return {"proxy_id": proxy.proxy_id, "updated_count": len(accounts), "account_ids": [account.id for account in accounts]}


def remove_proxy_assignments(db: Session, organization_id: str, actor_id: str, proxy_id: str, account_ids: list[str] | None = None) -> int:
    if _proxy_inventory_record(db, organization_id, proxy_id) is None and db.scalar(select(GmailAccount.id).where(GmailAccount.organization_id == organization_id, GmailAccount.proxy_id == proxy_id)) is None:
        raise ImportValidationError("Proxy assignment not found")
    query = select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.proxy_id == proxy_id, GmailAccount.lifecycle_status != "ARCHIVED").with_for_update()
    if account_ids:
        query = query.where(GmailAccount.id.in_(account_ids))
    accounts = db.scalars(query).all()
    for account in accounts:
        account.proxy_id = None
        account.proxy_label = None
        account.proxy_region = None
        account.proxy_status = "UNASSIGNED"
        if account.secret:
            payload = _safe_secret_payload(account)
            payload.pop("proxy", None)
            account.secret.encrypted_payload = SecretCipher().encrypt(payload)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="proxy.unassigned", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"proxy_id": proxy_id}))
    db.commit()
    return len(accounts)


def update_proxy(db: Session, organization_id: str, actor_id: str, proxy_id: str, proxy_value: str | None | object = _UNSET, label: str | None | object = _UNSET, region: str | None | object = _UNSET) -> dict:
    current_accounts = db.scalars(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.proxy_id == proxy_id, GmailAccount.lifecycle_status != "ARCHIVED").order_by(GmailAccount.email)).all()
    observation = _proxy_inventory_record(db, organization_id, proxy_id)
    if not current_accounts and not observation:
        raise ImportValidationError("Proxy assignment not found")
    if proxy_value is None:
        remove_proxy_assignments(db, organization_id, actor_id, proxy_id)
        return {"proxy_id": proxy_id, "updated_count": 0, "account_ids": []}
    if proxy_value is _UNSET:
        if current_accounts:
            return assign_proxy_accounts(db, organization_id, actor_id, [account.id for account in current_accounts], proxy_id=proxy_id, label=label, region=region)
        if observation:
            if label is not _UNSET:
                observation.label = label
            if region is not _UNSET:
                observation.region = region
            db.commit()
            return {"proxy_id": proxy_id, "updated_count": 0, "account_ids": []}
        raise ImportValidationError("Proxy assignment not found")
    if not current_accounts:
        try:
            proxy = parse_proxy(proxy_value)
        except ProxyValidationError as exc:
            raise ImportValidationError(str(exc)) from exc
        if proxy is None:
            raise ImportValidationError("Enter a proxy connection")
        observation = _upsert_proxy_inventory(db, organization_id, proxy, label if label is not _UNSET else None, region if region is not _UNSET else None)
        db.commit()
        return {"proxy_id": proxy.proxy_id, "updated_count": 0, "account_ids": []}
    return assign_proxy_accounts(db, organization_id, actor_id, [account.id for account in current_accounts], proxy_value=proxy_value, label=label, region=region)


def _probe_proxy(proxy) -> dict:
    settings = get_settings()
    request = Request(settings.proxy_test_url, headers={"User-Agent": "Northstar-Operations/1.0"})
    if proxy.authenticated and proxy.username is not None and proxy.password is not None:
        token = base64.b64encode(f"{proxy.username}:{proxy.password}".encode()).decode()
        request.add_header("Proxy-Authorization", f"Basic {token}")
    opener = build_opener(ProxyHandler({"http": proxy.server, "https": proxy.server}))
    started = time.monotonic()
    try:
        with opener.open(request, timeout=settings.proxy_test_timeout_seconds) as response:
            body = response.read(256_000)
        payload = json.loads(body.decode("utf-8"))
        connection = payload.get("connection") if isinstance(payload, dict) else {}
        connection = connection if isinstance(connection, dict) else {}
        if isinstance(payload, dict) and payload.get("success") is False:
            raise ValueError("proxy provider returned an unsuccessful response")
        return {"status": "HEALTHY", "public_ip": payload.get("ip"), "provider": connection.get("org") or connection.get("isp"), "country": payload.get("country"), "city": payload.get("city"), "latency_ms": max(0, round((time.monotonic() - started) * 1000))}
    except Exception as exc:  # noqa: BLE001
        logger.info("proxy health check failed (%s)", type(exc).__name__)
        return {"status": "UNREACHABLE", "public_ip": None, "provider": None, "country": None, "city": None, "latency_ms": None}


def test_proxy(db: Session, organization_id: str, actor_id: str, proxy_id: str) -> dict:
    proxy, _ = _proxy_config_for_id(db, organization_id, proxy_id)
    result = _probe_proxy(proxy)
    now = utcnow()
    observation = db.get(ProxyInventory, proxy_id)
    if not observation:
        observation = ProxyInventory(id=proxy_id, organization_id=organization_id)
    observation.provider = result["provider"]
    observation.country = result["country"]
    observation.city = result["city"]
    observation.public_ip = result["public_ip"]
    observation.test_status = result["status"]
    observation.test_error = None if result["status"] == "HEALTHY" else "Proxy did not respond to the health check"
    observation.latency_ms = result["latency_ms"]
    observation.last_tested_at = now
    db.add(observation)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="proxy.tested", target_type="proxy", target_id=proxy_id, result="success" if result["status"] == "HEALTHY" else "attention", metadata_json={"status": result["status"], "country": result["country"], "city": result["city"], "provider": result["provider"], "latency_ms": result["latency_ms"]}))
    db.commit()
    return {"proxy_id": proxy_id, **result, "tested_at": now, "error": observation.test_error}


def start_login_session(db: Session, organization_id: str, actor_id: str, account_id: str, purpose: str = "login", contacts_csv_path: str | None = None) -> BrowserSession:
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot open login sessions")
    profile = _ensure_browser_profile(db, account)
    if purpose not in {"login", "reuse", "contacts", "labels_markers", "security", "addons"}:
        raise ImportValidationError("Session purpose must be 'login', 'reuse', 'contacts', 'labels_markers', 'security', or 'addons'")
    if purpose in {"reuse", "contacts", "labels_markers", "security", "addons"} and profile.authenticated_at is None:
        raise ImportValidationError("No authenticated profile saved for this account — complete a login session first so the profile gets saved")
    if purpose == "contacts" and not contacts_csv_path:
        raise ImportValidationError("A contacts CSV file is required for a contacts session")
    if profile.active_session_id:
        active = db.scalar(select(BrowserSession).where(BrowserSession.id == profile.active_session_id, BrowserSession.state.in_(ACTIVE_SESSION_STATES)))
        if active:
            browser_opened = bool(active.metadata_json.get("browser_opened"))
            browser_alive = browser_opened and local_browser_is_alive(profile.id, active.id)
            if browser_alive or not get_settings().browser_launch_enabled:
                raise ImportValidationError("This account already has an open login session")
            _recover_stale_session(db, organization_id, actor_id, active)
    session_id = _new_id("session")
    launched, process_id = launch_local_browser(profile.id, session_id)
    logger.info("session %s: browser launch result=%s process_id=%s (browser_launch_enabled=%s, observer_enabled=%s)", session_id, launched, process_id, get_settings().browser_launch_enabled, get_settings().browser_observer_enabled)
    if not launched:
        # Nothing is persisted for this attempt, so the account/profile stay
        # clean and a later attempt can retry without a zombie session blocking it.
        raise BrowserLaunchError("Browser could not be launched (check BROWSER_LAUNCH_ENABLED and BROWSER_EXECUTABLE)")
    existing_auth = purpose in {"reuse", "contacts", "labels_markers", "security", "addons"}
    addon_candidates = select_addon_candidates(3) if purpose == "addons" else []
    session_metadata = {"boundary": "human_login", "purpose": purpose, "browser_opened": True, "process_id": process_id, "contacts_csv_path": contacts_csv_path}
    if purpose == "addons":
        session_metadata.update({"addons_requested": 3, "addons_candidates": addon_candidates})
    session = BrowserSession(id=session_id, organization_id=organization_id, account_id=account.id, profile_id=profile.id, state="RUNNING" if existing_auth else "LOGIN_REQUIRED", login_state="AUTHENTICATED" if existing_auth else "REQUIRED", runtime_type="LOCAL_CHROME", metadata_json=session_metadata)
    profile.status = "OPEN"
    profile.lock_state = "LOCKED"
    profile.active_session_id = session.id
    profile.last_opened_at = utcnow()
    if not existing_auth:
        account.login_status = "REQUIRED"
        account.profile_status = "OPEN"
    db.add(session)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.started", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account.id, "profile_id": profile.id, "runtime": "LOCAL_CHROME"}))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.browser_opened", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account.id, "profile_id": profile.id, "runtime": "LOCAL_CHROME"}))
    db.commit()
    observer_started = _start_observer(session.id, profile.id, organization_id, actor_id, purpose=purpose, contacts_csv_path=contacts_csv_path, addon_candidates=addon_candidates)
    logger.info("session %s: observer started=%s", session.id, observer_started)
    return get_session(db, organization_id, session.id)


def contact_file_response(contact_file: ContactFile) -> dict:
    return {
        "id": contact_file.id,
        "original_filename": contact_file.original_filename,
        "media_type": contact_file.media_type,
        "size_bytes": contact_file.size_bytes,
        "contact_count": contact_file.contact_count,
        "status": contact_file.status,
        "last_used_at": contact_file.last_used_at,
        "created_at": contact_file.created_at,
        "updated_at": contact_file.updated_at,
    }


def list_contact_files(db: Session, organization_id: str, limit: int = 100) -> list[dict]:
    query = select(ContactFile).where(ContactFile.organization_id == organization_id).order_by(ContactFile.created_at.desc()).limit(limit)
    return [contact_file_response(item) for item in db.scalars(query).all()]


def add_contact_file(db: Session, organization_id: str, actor_id: str, filename: str, media_type: str | None, content: bytes) -> ContactFile:
    try:
        safe_name, contact_count, detected_type = validate_contact_file(filename, content)
    except ValueError as exc:
        raise ImportValidationError(str(exc)) from exc
    checksum = contact_file_checksum(content)
    duplicate = db.scalar(select(ContactFile).where(ContactFile.organization_id == organization_id, ContactFile.checksum == checksum))
    if duplicate:
        raise ImportValidationError("This contact file is already in the library")
    file_id = new_contact_file_id()
    path = write_contact_file(organization_id, file_id, safe_name, content)
    contact_file = ContactFile(id=file_id, organization_id=organization_id, original_filename=safe_name, storage_reference=str(path.relative_to(Path(get_settings().profile_root))), media_type=detected_type, size_bytes=len(content), contact_count=contact_count, checksum=checksum, status="READY", created_by=actor_id)
    try:
        db.add(contact_file)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="contact_file.uploaded", target_type="contact_file", target_id=file_id, result="success", metadata_json={"filename": safe_name, "size_bytes": len(content), "contact_count": contact_count}))
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return contact_file


def delete_contact_file(db: Session, organization_id: str, actor_id: str, file_id: str) -> None:
    contact_file = db.scalar(select(ContactFile).where(ContactFile.organization_id == organization_id, ContactFile.id == file_id))
    if not contact_file:
        raise ImportValidationError("Contact file not found")
    active_sessions = db.scalars(select(BrowserSession).where(BrowserSession.organization_id == organization_id, BrowserSession.state.in_(ACTIVE_SESSION_STATES))).all()
    if any((session.metadata_json or {}).get("contact_file_id") == file_id for session in active_sessions):
        raise ImportValidationError("This contact file is being used by an active import")
    path = Path(get_settings().profile_root) / contact_file.storage_reference
    path.unlink(missing_ok=True)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="contact_file.deleted", target_type="contact_file", target_id=file_id, result="success", metadata_json={"filename": contact_file.original_filename}))
    db.delete(contact_file)
    db.commit()


def start_contacts_import(db: Session, organization_id: str, actor_id: str, account_id: str, contact_file_id: str | None = None, raw_emails: str | None = None) -> tuple[BrowserSession, ContactFile | None, int]:
    """Open a profile-backed Google Contacts import for a library asset.

    ``raw_emails`` remains a compatibility path for older clients; the UI uses
    the reusable contact-file library and never sends inline contact data.
    """
    contact_file: ContactFile | None = None
    temporary_path: Path | None = None
    if contact_file_id:
        contact_file = db.scalar(select(ContactFile).where(ContactFile.organization_id == organization_id, ContactFile.id == contact_file_id))
        if not contact_file or contact_file.status != "READY":
            raise ImportValidationError("Choose a ready contact file")
        csv_path = Path(get_settings().profile_root) / contact_file.storage_reference
        email_count = contact_file.contact_count
        if not csv_path.is_file():
            raise ImportValidationError("The selected contact file is missing from storage")
    elif raw_emails:
        emails = parse_emails(raw_emails)
        temporary_path = write_contacts_upload(account_id, build_google_contacts_csv(emails))
        csv_path = temporary_path
        email_count = len(emails)
    else:
        raise ImportValidationError("Choose a ready contact file")
    try:
        session = start_login_session(db, organization_id, actor_id, account_id, purpose="contacts", contacts_csv_path=str(csv_path))
    except Exception:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        raise
    metadata = {**(session.metadata_json or {}), "contact_count": email_count}
    if contact_file:
        metadata.update({"contact_file_id": contact_file.id, "contact_file_name": contact_file.original_filename})
        contact_file.last_used_at = utcnow()
    session.metadata_json = metadata
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="contact_import.started", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account_id, "contact_file_id": contact_file.id if contact_file else None, "contact_count": email_count}))
    db.commit()
    return get_session(db, organization_id, session.id), contact_file, email_count


def start_labels_markers_operation(db: Session, organization_id: str, actor_id: str, account_id: str) -> BrowserSession:
    """Start the authenticated-profile Gmail settings operation."""
    return start_login_session(db, organization_id, actor_id, account_id, purpose="labels_markers")


def start_security_operation(db: Session, organization_id: str, actor_id: str, account_id: str) -> BrowserSession:
    """Start Enhanced Safe Browsing verification in the saved profile."""
    return start_login_session(db, organization_id, actor_id, account_id, purpose="security")


def start_addons_operation(db: Session, organization_id: str, actor_id: str, account_id: str) -> BrowserSession:
    """Start the bounded Marketplace installation in the saved profile."""
    return start_login_session(db, organization_id, actor_id, account_id, purpose="addons")


def record_session_event(db: Session, organization_id: str, actor_id: str, session_id: str, event_type: str, detail: str | None = None) -> BrowserSession:
    session = get_session(db, organization_id, session_id)
    if not session:
        raise ImportValidationError("Session not found")
    if session.state not in ACTIVE_SESSION_STATES:
        raise ImportValidationError("This session is already closed")
    now = utcnow()
    session.last_event_at = now
    if event_type == "LOGIN_COMPLETED":
        session.state = "RUNNING"
        session.login_state = "AUTHENTICATED"
        session.account.login_status = "AUTHENTICATED"
        session.profile.authenticated_at = now
        session.account.profile_status = "SAVED"
    elif event_type == "VERIFICATION_REQUIRED":
        session.state = "NEEDS_ATTENTION"
        session.login_state = "VERIFICATION_REQUIRED"
        session.account.login_status = "VERIFICATION_REQUIRED"
        session.failure_reason = "Google requested additional verification"
    elif event_type == "UNKNOWN_PAGE":
        session.state = "NEEDS_ATTENTION"
        session.login_state = "UNKNOWN_PAGE"
        session.failure_reason = "The worker encountered an unrecognized page"
    elif event_type == "ACCOUNT_NOT_FOUND":
        # This is a definitive terminal result, not a human challenge. Close
        # the dedicated browser, release the profile lock, and remove the
        # unsaved scratch directory after Chromium has fully closed. The durable session remains
        # in history as CLOSED with ACCOUNT_NOT_FOUND for audit/status views.
        close_local_browser(session.id)
        observer_stopped = _stop_observer(session.id)
        session.state = "CLOSED"
        session.login_state = "ACCOUNT_NOT_FOUND"
        session.account.login_status = "NONEXISTENT"
        session.account.readiness_status = "SETUP_ERROR"
        session.failure_reason = "Google reports that no account exists for this email address"
        session.closed_at = now
        session.profile.status = "RESERVED"
        session.profile.lock_state = "UNLOCKED"
        session.profile.active_session_id = None
        session.profile.last_closed_at = now
        metadata = dict(session.metadata_json or {})
        metadata["browser_opened"] = False
        metadata["terminal_result"] = "ACCOUNT_NOT_FOUND"
        session.metadata_json = metadata
        if not observer_stopped:
            _prune_unsaved_profile_folder(session)
    elif event_type == "CONTACTS_IMPORTED":
        close_local_browser(session.id)
        _stop_observer(session.id)
        session.state = "CLOSED"
        session.login_state = "CONTACTS_IMPORTED"
        session.account.login_status = "AUTHENTICATED"
        session.account.profile_status = "SAVED"
        session.closed_at = now
        session.profile.status = "RESERVED"
        session.profile.lock_state = "UNLOCKED"
        session.profile.active_session_id = None
        session.profile.last_closed_at = now
        metadata = dict(session.metadata_json or {})
        metadata["browser_opened"] = False
        metadata["terminal_result"] = "CONTACTS_IMPORTED"
        session.metadata_json = metadata
    elif event_type == "SECURITY_COMPLETED":
        close_local_browser(session.id)
        _stop_observer(session.id)
        session.state = "CLOSED"
        session.login_state = "SECURITY_COMPLETED"
        session.account.login_status = "AUTHENTICATED"
        session.account.profile_status = "SAVED"
        session.closed_at = now
        session.profile.status = "RESERVED"
        session.profile.lock_state = "UNLOCKED"
        session.profile.active_session_id = None
        session.profile.last_closed_at = now
        metadata = dict(session.metadata_json or {})
        metadata["browser_opened"] = False
        metadata["terminal_result"] = "SECURITY_COMPLETED"
        session.metadata_json = metadata
    elif event_type == "HEARTBEAT":
        pass
    job = None
    if event_type in {"LOGIN_COMPLETED", "VERIFICATION_REQUIRED", "UNKNOWN_PAGE", "ACCOUNT_NOT_FOUND"}:
        job = _sync_job_item_for_session(db, session.id, session.state)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action=f"session.{event_type.lower()}", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": session.account_id}))
    db.commit()
    _maybe_dispatch_next(db, job)
    return get_session(db, organization_id, session.id)


def close_session(db: Session, organization_id: str, actor_id: str, session_id: str) -> BrowserSession:
    session = get_session(db, organization_id, session_id)
    if not session:
        raise ImportValidationError("Session not found")
    logger.info("session %s: closing (state=%s)", session_id, session.state)
    if session.state == "CLOSED":
        return session
    close_local_browser(session.id)
    observer_stopped = _stop_observer(session.id)
    now = utcnow()
    session.state = "CLOSED"
    session.last_event_at = now
    session.closed_at = now
    session.profile.status = "RESERVED"
    session.profile.lock_state = "UNLOCKED"
    session.profile.active_session_id = None
    session.profile.last_closed_at = now
    if session.login_state == "REQUIRED" and session.profile.authenticated_at is None:
        session.account.login_status = "NOT_STARTED"
        session.account.profile_status = "RESERVED"
    elif session.profile.authenticated_at is not None:
        session.account.login_status = "AUTHENTICATED"
        session.account.profile_status = "SAVED"
    if not observer_stopped:
        _prune_unsaved_profile_folder(session)
    job = _sync_job_item_for_session(db, session.id, session.state)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.closed", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": session.account_id, "profile_id": session.profile_id}))
    db.commit()
    _maybe_dispatch_next(db, job)
    return get_session(db, organization_id, session.id)


def observe_session_page(db: Session, organization_id: str, actor_id: str, session_id: str, url: str, title: str, text: str) -> tuple[str, str, BrowserSession]:
    session = get_session(db, organization_id, session_id)
    if not session:
        raise ImportValidationError("Session not found")
    if session.state not in ACTIVE_SESSION_STATES:
        raise ImportValidationError("This session is already closed")
    signal, action = classify_page(PageObservation(url=url, title=title, text=text))
    parsed = urlparse(url)
    metadata = {"account_id": session.account_id, "signal": signal.value, "host": parsed.hostname or "unknown", "path": parsed.path[:160]}
    if signal in {"CAPTCHA", "MFA_OR_SECURITY_CHECK"}:
        if signal == "MFA_OR_SECURITY_CHECK" and _account_has_totp_secret(db, session):
            logger.info("session %s: MFA step on %s; auto sign-in will enter the TOTP", session_id, url)
        else:
            session.failure_reason = "Google requested additional verification"
            logger.warning("session %s: challenge detected on %s -> NEEDS_ATTENTION", session_id, url)
            updated = record_session_event(db, organization_id, actor_id, session_id, "VERIFICATION_REQUIRED")
            return signal.value, action.value, updated
    if signal == "UNKNOWN":
        logger.warning("session %s: unknown page %s -> NEEDS_ATTENTION", session_id, url)
        updated = record_session_event(db, organization_id, actor_id, session_id, "UNKNOWN_PAGE")
        return signal.value, action.value, updated
    if signal == "ACCOUNT_NOT_FOUND":
        logger.warning("session %s: Google reports the account does not exist (%s) -> NEEDS_ATTENTION", session_id, url)
        updated = record_session_event(db, organization_id, actor_id, session_id, "ACCOUNT_NOT_FOUND")
        return signal.value, action.value, updated
    if signal == "GMAIL_HOME" and session.login_state != "AUTHENTICATED":
        logger.info("session %s: Gmail reached on %s -> login completed", session_id, url)
        updated = record_session_event(db, organization_id, actor_id, session_id, "LOGIN_COMPLETED")
        return signal.value, action.value, updated
    if signal == "GOOGLE_ACCOUNT_HOME" and session.login_state != "AUTHENTICATED":
        logger.info("session %s: signed-in Google Account home reached on %s -> login completed", session_id, url)
        updated = record_session_event(db, organization_id, actor_id, session_id, "LOGIN_COMPLETED")
        return signal.value, action.value, updated
    session.last_event_at = utcnow()
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.page_observed", target_type="browser_session", target_id=session.id, result="success", metadata_json=metadata))
    db.commit()
    return signal.value, action.value, get_session(db, organization_id, session.id)


def login_job_progress(db: Session, organization_id: str, job_id: str) -> dict | None:
    """Fine-grained progress for a batch: per-item state plus job counters."""
    job = db.scalar(select(LoginJob).options(joinedload(LoginJob.items).joinedload(LoginJobItem.account)).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    if not job:
        return None
    states: dict[str, int] = {}
    items: list[dict] = []
    for item in job.items:
        states[item.state] = states.get(item.state, 0) + 1
        items.append({"id": item.id, "account_id": item.account_id, "account_email": item.account.email, "session_id": item.session_id, "state": item.state, "attempts": item.attempts, "last_error": item.last_error, "paused_reason": item.paused_reason, "updated_at": item.updated_at, "completed_at": item.completed_at})
    resolved = sum(count for state, count in states.items() if state in {"SUCCEEDED", "ATTENTION", "FAILED", "CANCELLED"})
    total = job.total_count or 0
    return {
        "id": job.id,
        "status": job.status,
        "concurrency_limit": job.concurrency_limit,
        "total_count": total,
        "queued_count": job.queued_count,
        "running_count": job.running_count,
        "succeeded_count": job.succeeded_count,
        "attention_count": job.attention_count,
        "failed_count": job.failed_count,
        "cancelled_count": states.get("CANCELLED", 0),
        "progress_percent": round((resolved / total) * 100) if total else 0,
        "states": states,
        "items": items,
    }


def resume_login_job(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    """Resume a paused batch: return to RUNNING and redispatch queued items."""
    job = db.scalar(select(LoginJob).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    if not job:
        raise ImportValidationError("Login batch not found")
    if job.status != "PAUSED":
        raise ImportValidationError(f"Cannot resume a {job.status.lower()} batch")
    job.status = "RUNNING"
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="login_job.resumed", target_type="login_job", target_id=job.id, result="success", metadata_json={"concurrency_limit": job.concurrency_limit}))
    db.commit()
    return dispatch_login_job(db, organization_id, actor_id, job_id)


def refresh_worker_heartbeats(db: Session, organization_id: str, worker_id: str) -> int:
    """Refresh Redis lock TTLs and database heartbeat timestamps for this worker's leases."""
    settings = get_settings()
    locks = _locks()
    now = utcnow()
    items = db.scalars(select(LoginJobItem).where(LoginJobItem.organization_id == organization_id, LoginJobItem.state == "RUNNING", LoginJobItem.worker_id == worker_id)).all()
    for item in items:
        item.heartbeat_at = now
        lock = db.scalar(select(JobLock).where(JobLock.released == False, JobLock.account_id == item.account_id, JobLock.worker_id == worker_id))
        if item.lock_key:
            locks.refresh(item.account_id, item.lock_key, settings.worker_lock_ttl * 1000)
        if lock:
            lock.heartbeat_at = now
    db.commit()
    return len(items)


def recover_stale_locks(db: Session, organization_id: str) -> dict:
    """Detect orphaned worker leases and either retry or fail their job items.

    A lease is stale when its last heartbeat is older than the stale threshold.
    Recovered items are re-queued when attempts remain, otherwise marked FAILED.
    """
    settings = get_settings()
    queue = _queue()
    threshold = utcnow() - timedelta(seconds=settings.worker_stale_seconds)
    now = utcnow()

    stale_rows = db.scalars(select(JobLock).where(JobLock.released == False, JobLock.heartbeat_at < threshold)).all()
    for lock_row in stale_rows:
        lock_row.released = True
        lock_row.released_at = now
        lock_row.reason = "stale_worker_recovered"
        lock_row.heartbeat_at = None
        _locks().force_release(lock_row.account_id)

    recovered: dict[str, int] = {"requeued": 0, "failed": 0, "released": 0}
    for lock_row in stale_rows:
        recovered["released"] += 1

    stale_items = db.scalars(select(LoginJobItem).where(LoginJobItem.organization_id == organization_id, LoginJobItem.state == "RUNNING", LoginJobItem.worker_id.is_not(None), LoginJobItem.heartbeat_at < threshold)).all()
    for item in stale_items:
        job = db.get(LoginJob, item.job_id)
        if item.session_id:
            session = db.scalar(select(BrowserSession).where(BrowserSession.organization_id == organization_id, BrowserSession.id == item.session_id))
            if session and session.state in ACTIVE_SESSION_STATES:
                close_local_browser(session.id)
                _stop_observer(session.id)
                session.state = "CLOSED"
                session.closed_at = now
                session.last_event_at = now
                session.profile.status = "RESERVED"
                session.profile.lock_state = "UNLOCKED"
                session.profile.active_session_id = None
                session.profile.last_closed_at = now
                session.account.login_status = "NOT_STARTED"
        _release_item_lease(db, item)
        item.session_id = None
        item.worker_id = None
        item.heartbeat_at = None
        item.attempts += 1
        if item.attempts >= settings.worker_retry_limit:
            item.state = "FAILED"
            item.last_error = "Stale worker exceeded the retry limit"
            item.completed_at = now
            if job:
                _bump_job(db, job, running_count=-1, failed_count=1)
            db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id="worker", action="login_job.item_failed", target_type="login_job_item", target_id=item.id, result="failed", metadata_json={"job_id": item.job_id, "account_id": item.account_id, "reason": "stale_worker_exhausted_retries"}))
            recovered["failed"] += 1
        else:
            item.state = "QUEUED"
            item.last_error = "Stale worker recovered; retrying"
            item.completed_at = None
            if job:
                _bump_job(db, job, running_count=-1, queued_count=1)
            queue.requeue(item.job_id, item.id, item.account_id)
            db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id="worker", action="login_job.item_requeued", target_type="login_job_item", target_id=item.id, result="retry", metadata_json={"job_id": item.job_id, "account_id": item.account_id, "reason": "stale_worker_recovered", "attempt": item.attempts}))
            recovered["requeued"] += 1
        if job and job.queued_count == 0 and job.running_count == 0:
            job.status = "COMPLETED"
            job.completed_at = now

    db.commit()
    return recovered
