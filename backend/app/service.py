import os
import re
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta
from logging import getLogger

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from .config import get_settings
from .db import SessionLocal
from .models import AccountGroup, AccountSecret, AuditEvent, BrowserProfile, BrowserSession, CredentialRevision, GmailAccount, ImportOperation, JobLock, LoginJob, LoginJobItem, OperationDraft, utcnow
from .schemas import AccountStructuredInput, BulkPreviewResponse, PreviewRow
from .security import SecretCipher, mask_recovery
from .runtime import browser_debug_port, close_local_browser, launch_local_browser, local_browser_is_alive
from .observer import BrowserObserver
from .login_policy import PageObservation, classify_page
from .worker.locks import RedisLocks, account_lock_key
from .worker.queue import RedisQueue
from urllib.parse import urlparse

logger = getLogger(__name__)

WORKER_PREFIX = "worker-"
API_WORKER_ID = "orchestrator-api"
OPERATION_TYPES = {"contacts", "security", "labels", "apps", "mail", "addons", "alerts"}
FORBIDDEN_OPERATION_KEY_PARTS = ("password", "recovery", "2fa", "totp", "cookie", "token", "secret")


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
    existing = set(db.scalars(select(GmailAccount.normalized_email).where(GmailAccount.organization_id == organization_id, GmailAccount.normalized_email.in_([line.email for line in parsed]))).all())
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


def _add_credential_revision(db: Session, organization_id: str, account_id: str, payload: dict[str, str], recovery_hint: str, two_fa_configured: bool, actor_id: str, version: int = 1) -> None:
    db.add(CredentialRevision(id=_new_id("credrev"), organization_id=organization_id, account_id=account_id, version=version, secret_ref=f"secret://account/{account_id}/v{version}", encrypted_payload=SecretCipher().encrypt(payload), recovery_hint=recovery_hint, two_fa_configured=two_fa_configured, status="ACTIVE", created_by=actor_id))


def add_account(db: Session, organization_id: str, actor_id: str, data: AccountStructuredInput) -> GmailAccount:
    group = db.scalar(select(AccountGroup).where(AccountGroup.id == data.group_id, AccountGroup.organization_id == organization_id))
    if not group:
        raise ImportValidationError("Choose a valid destination group")
    email = data.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ImportValidationError("Enter a valid email address")
    if db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.normalized_email == email)):
        raise ImportValidationError("An account with this email already exists")
    account_id = _new_id("acct")
    account = GmailAccount(id=account_id, organization_id=organization_id, group_id=group.id, email=email, normalized_email=email, lifecycle_status="SETUP_REQUIRED", readiness_status="READY_FOR_LOGIN", recovery_hint=mask_recovery(data.recovery), two_fa_configured=bool(data.two_fa), proxy_label=data.proxy_label, proxy_region=data.proxy_region, proxy_status="UNASSIGNED", profile_id=f"profile_{uuid.uuid4().hex[:12]}", profile_storage_ref=f"profiles/{organization_id}/{account_id}", profile_status="RESERVED")
    secret = AccountSecret(id=_new_id("secret"), account_id=account_id, secret_ref=f"secret://account/{account_id}/primary", encrypted_payload=SecretCipher().encrypt({"password": data.password, "recovery": data.recovery, "2fa": data.two_fa}))
    db.add(account)
    db.add(secret)
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
        account_id = _new_id("acct")
        account = GmailAccount(id=account_id, organization_id=organization_id, group_id=group_id, email=line.email, normalized_email=line.email, lifecycle_status="SETUP_REQUIRED", readiness_status="READY_FOR_LOGIN", recovery_hint=mask_recovery(line.recovery), two_fa_configured=bool(line.two_fa), login_status="NOT_STARTED", gmail_status="NOT_CHECKED", proxy_status="UNASSIGNED", profile_id=f"profile_{uuid.uuid4().hex[:12]}", profile_storage_ref=f"profiles/{organization_id}/{account_id}", profile_status="RESERVED")
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
    return {"id": account.id, "email": account.email, "group_id": account.group_id, "group_name": account.group.name, "lifecycle_status": account.lifecycle_status, "readiness_status": account.readiness_status, "login_status": account.login_status, "gmail_status": account.gmail_status, "secret_status": "STORED_ENCRYPTED" if account.secret else "MISSING", "recovery_masked": account.recovery_hint, "two_fa_configured": account.two_fa_configured, "proxy": {"id": account.proxy_id, "label": account.proxy_label, "region": account.proxy_region, "status": account.proxy_status}, "profile": {"id": account.profile_id, "storage_reference": account.profile_storage_ref, "status": account.profile_status}, "created_at": account.created_at, "updated_at": account.updated_at}


def list_accounts(db: Session, organization_id: str, q: str | None, group_id: str | None, status: str | None, page: int, page_size: int) -> dict:
    filters = [GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED"]
    if q:
        term = f"%{q.strip().lower()}%"
        filters.append(or_(GmailAccount.email.ilike(term), AccountGroup.name.ilike(term)))
    if group_id:
        filters.append(GmailAccount.group_id == group_id)
    if status:
        filters.append(GmailAccount.readiness_status == status)
    total = db.scalar(select(func.count(GmailAccount.id)).join(AccountGroup).where(*filters)) or 0
    items = db.scalars(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret)).join(AccountGroup).where(*filters).order_by(GmailAccount.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": [account_response(item) for item in items], "page": page, "page_size": page_size, "total": total}


def get_account(db: Session, organization_id: str, account_id: str) -> GmailAccount | None:
    return db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))


def update_account_metadata(db: Session, organization_id: str, actor_id: str, account_id: str, proxy_label: str | None, proxy_region: str | None) -> dict:
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot be edited")
    account.proxy_label = proxy_label
    account.proxy_region = proxy_region
    account.proxy_status = "ASSIGNED" if proxy_label or proxy_region else "UNASSIGNED"
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.metadata_updated", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"proxy_label_changed": True, "proxy_region_changed": True, "proxy_status": account.proxy_status}))
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
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account:
        raise ImportValidationError("Account not found")
    latest = db.scalar(select(func.max(CredentialRevision.version)).where(CredentialRevision.account_id == account.id)) or 0
    for revision in db.scalars(select(CredentialRevision).where(CredentialRevision.account_id == account.id, CredentialRevision.status == "ACTIVE")).all():
        revision.status = "RETIRED"
    encrypted_payload = SecretCipher().encrypt({"password": password, "recovery": recovery, "2fa": two_fa})
    if account.secret:
        account.secret.encrypted_payload = encrypted_payload
        account.secret.key_version = "v1"
    else:
        account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", encrypted_payload=encrypted_payload)
    account.recovery_hint = mask_recovery(recovery)
    account.two_fa_configured = bool(two_fa)
    account.readiness_status = "READY_FOR_LOGIN"
    account.login_status = "REQUIRED"
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


def _start_observer(session_id: str, profile_id: str, organization_id: str, actor_id: str) -> bool:
    settings = get_settings()
    if not settings.browser_observer_enabled:
        logger.warning("AUTOMATION DISABLED: session %s opened a browser window but BROWSER_OBSERVER_ENABLED is false; automation will not run", session_id)
        _record_observer_status(session_id, organization_id, actor_id, False, "BROWSER_OBSERVER_ENABLED is false")
        return False
    cdp_port = browser_debug_port(session_id)
    if not cdp_port:
        logger.warning("session %s: browser window opened but no CDP endpoint was detected; automation cannot attach (check BROWSER_OBSERVER_ENABLED and playwright)", session_id)
        _record_observer_status(session_id, organization_id, actor_id, False, "no CDP endpoint detected")
        return False
    observer = BrowserObserver(
        session_id,
        profile_id,
        cdp_port,
        observe_session_page=lambda sid, url, title, text: _observe_in_own_session(organization_id, actor_id, sid, url, title, text),
        resolve_credentials=lambda sid: _resolve_login_credentials(sid),
        report_event=_report_observer_event,
        interval=settings.browser_observe_interval,
        settle_seconds=settings.browser_observe_settle_seconds,
    )
    _observers[session_id] = observer
    observer.start()
    _record_observer_status(session_id, organization_id, actor_id, True)
    logger.info("AUTOMATION ACTIVE: observer attached to session %s on CDP port %s", session_id, cdp_port)
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


def _stop_observer(session_id: str) -> None:
    observer = _observers.pop(session_id, None)
    if observer:
        observer.stop()


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


def _report_observer_event(session_id: str, event: str, detail: str = "", result: str = "success") -> None:
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session:
            return
        db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action=f"session.auto_login.{event}", target_type="browser_session", target_id=session.id, result=result, metadata_json={"detail": detail[:300], "account_id": session.account_id}))
        db.commit()
        logger.info("session %s: auto-login %s %s", session_id, event, result)
    finally:
        db.close()


def _recover_stale_session(db: Session, organization_id: str, actor_id: str, session: BrowserSession) -> None:
    """Release a durable session row whose dedicated browser is gone."""
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
    if session.login_state == "REQUIRED":
        session.account.login_status = "NOT_STARTED"
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
    # Pause the affected account whenever Google posed a challenge or the
    # worker hit an unknown page; the profile stays reserved for a human.
    if session_state == "RUNNING":
        final_item_state = "SUCCEEDED"
        error = None
    elif session_state == "NEEDS_ATTENTION":
        item.paused_reason = "Challenge or unknown page raised for operator attention"
        return _finalize_item(db, item, "ATTENTION")
    elif session_state == "CLOSED":
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


def session_response(session: BrowserSession) -> dict:
    return {
        "id": session.id,
        "account_id": session.account_id,
        "account_email": session.account.email,
        "profile_id": session.profile_id,
        "profile_storage_reference": session.profile.storage_reference,
        "profile_status": session.profile.status,
        "state": session.state,
        "login_state": session.login_state,
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


def list_sessions(db: Session, organization_id: str, account_id: str | None, limit: int = 25) -> list[dict]:
    query = select(BrowserSession).options(joinedload(BrowserSession.account), joinedload(BrowserSession.profile)).where(BrowserSession.organization_id == organization_id).order_by(BrowserSession.created_at.desc()).limit(limit)
    if account_id:
        query = query.where(BrowserSession.account_id == account_id)
    return [session_response(item) for item in db.scalars(query).unique().all()]


def start_login_session(db: Session, organization_id: str, actor_id: str, account_id: str) -> BrowserSession:
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot open login sessions")
    profile = _ensure_browser_profile(db, account)
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
    session = BrowserSession(id=session_id, organization_id=organization_id, account_id=account.id, profile_id=profile.id, state="LOGIN_REQUIRED", login_state="REQUIRED", runtime_type="LOCAL_CHROME", metadata_json={"boundary": "human_login", "browser_opened": True, "process_id": process_id})
    profile.status = "OPEN"
    profile.lock_state = "LOCKED"
    profile.active_session_id = session.id
    profile.last_opened_at = utcnow()
    account.login_status = "REQUIRED"
    db.add(session)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.started", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account.id, "profile_id": profile.id, "runtime": "LOCAL_CHROME"}))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.browser_opened", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account.id, "profile_id": profile.id, "runtime": "LOCAL_CHROME"}))
    db.commit()
    observer_started = _start_observer(session.id, profile.id, organization_id, actor_id)
    logger.info("session %s: observer started=%s", session.id, observer_started)
    return get_session(db, organization_id, session.id)


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
    elif event_type == "VERIFICATION_REQUIRED":
        session.state = "NEEDS_ATTENTION"
        session.login_state = "VERIFICATION_REQUIRED"
        session.account.login_status = "VERIFICATION_REQUIRED"
        session.failure_reason = "Google requested additional verification"
    elif event_type == "UNKNOWN_PAGE":
        session.state = "NEEDS_ATTENTION"
        session.login_state = "UNKNOWN_PAGE"
        session.failure_reason = "The worker encountered an unrecognized page"
    elif event_type == "HEARTBEAT":
        pass
    job = None
    if event_type in {"LOGIN_COMPLETED", "VERIFICATION_REQUIRED", "UNKNOWN_PAGE"}:
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
    _stop_observer(session.id)
    now = utcnow()
    session.state = "CLOSED"
    session.last_event_at = now
    session.closed_at = now
    session.profile.status = "RESERVED"
    session.profile.lock_state = "UNLOCKED"
    session.profile.active_session_id = None
    session.profile.last_closed_at = now
    if session.login_state == "REQUIRED":
        session.account.login_status = "NOT_STARTED"
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
        session.failure_reason = "Google requested additional verification"
        logger.warning("session %s: challenge detected on %s -> NEEDS_ATTENTION", session_id, url)
        updated = record_session_event(db, organization_id, actor_id, session_id, "VERIFICATION_REQUIRED")
        return signal.value, action.value, updated
    if signal == "UNKNOWN":
        logger.warning("session %s: unknown page %s -> NEEDS_ATTENTION", session_id, url)
        updated = record_session_event(db, organization_id, actor_id, session_id, "UNKNOWN_PAGE")
        return signal.value, action.value, updated
    if signal == "GMAIL_HOME" and session.login_state != "AUTHENTICATED":
        logger.info("session %s: Gmail reached on %s -> login completed", session_id, url)
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
