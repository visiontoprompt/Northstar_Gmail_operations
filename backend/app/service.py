import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from .models import AccountGroup, AccountSecret, AuditEvent, BrowserProfile, BrowserSession, CredentialRevision, GmailAccount, ImportOperation, LoginJob, LoginJobItem, utcnow
from .schemas import AccountStructuredInput, BulkPreviewResponse, PreviewRow
from .security import SecretCipher, mask_recovery
from .runtime import close_local_browser, launch_local_browser
from .login_policy import PageObservation, classify_page
from urllib.parse import urlparse

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ImportValidationError(ValueError):
    pass


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
    filters = [GmailAccount.organization_id == organization_id]
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
    query = select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.readiness_status == "READY_FOR_LOGIN")
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
    transitions = {"QUEUED": {"PAUSED", "CANCELLED", "RUNNING"}, "RUNNING": {"PAUSED", "CANCELLED"}, "PAUSED": {"QUEUED", "CANCELLED"}}
    if next_state not in transitions.get(job.status, set()):
        raise ImportValidationError(f"Cannot move a {job.status.lower()} batch to {next_state.lower()}")
    job.status = next_state
    if next_state == "RUNNING" and not job.started_at:
        job.started_at = utcnow()
    if next_state == "CANCELLED":
        for item in db.scalars(select(LoginJobItem).where(LoginJobItem.job_id == job.id, LoginJobItem.state.in_(["QUEUED", "RUNNING"]))).all():
            if item.session_id:
                session = get_session(db, organization_id, item.session_id)
                if session and session.state in ACTIVE_SESSION_STATES:
                    close_local_browser(session.id)
                    now = utcnow()
                    session.state = "CLOSED"
                    session.closed_at = now
                    session.last_event_at = now
                    session.profile.status = "RESERVED"
                    session.profile.lock_state = "UNLOCKED"
                    session.profile.active_session_id = None
                    session.profile.last_closed_at = now
            item.state = "CANCELLED"
        job.running_count = 0
        job.queued_count = 0
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action=f"login_job.{next_state.lower()}", target_type="login_job", target_id=job.id, result="success", metadata_json={"concurrency_limit": job.concurrency_limit}))
    db.commit()
    return get_login_job(db, organization_id, job.id)


def dispatch_login_job(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    job = db.scalar(select(LoginJob).options(joinedload(LoginJob.items).joinedload(LoginJobItem.account)).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    if not job:
        raise ImportValidationError("Login batch not found")
    if job.status != "RUNNING":
        raise ImportValidationError("Start the login batch before dispatching profiles")
    slots = max(0, job.concurrency_limit - job.running_count)
    queued = [item for item in job.items if item.state == "QUEUED"][:slots]
    for item in queued:
        try:
            session = start_login_session(db, organization_id, actor_id, item.account_id)
        except ImportValidationError as exc:
            item.state = "ATTENTION"
            item.last_error = "Account could not be opened for this batch"
            job.queued_count = max(0, job.queued_count - 1)
            job.attention_count += 1
            db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="login_job.item_attention", target_type="login_job_item", target_id=item.id, result="attention", metadata_json={"job_id": job.id, "account_id": item.account_id, "reason": "profile_unavailable"}))
            continue
        item.session_id = session["id"] if isinstance(session, dict) else session.id
        item.state = "RUNNING"
        item.attempts += 1
        job.queued_count = max(0, job.queued_count - 1)
        job.running_count += 1
    if job.queued_count == 0 and job.running_count == 0:
        job.status = "COMPLETED"
        job.completed_at = utcnow()
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="login_job.dispatched", target_type="login_job", target_id=job.id, result="success", metadata_json={"opened_count": len([item for item in queued if item.state == "RUNNING"]), "concurrency_limit": job.concurrency_limit}))
    db.commit()
    return get_login_job(db, organization_id, job.id)


def resume_login_job(db: Session, organization_id: str, actor_id: str, job_id: str) -> dict:
    job = db.scalar(select(LoginJob).where(LoginJob.organization_id == organization_id, LoginJob.id == job_id))
    if not job:
        raise ImportValidationError("Login batch not found")
    if job.status == "PAUSED":
        update_login_job_state(db, organization_id, actor_id, job_id, "QUEUED")
    update_login_job_state(db, organization_id, actor_id, job_id, "RUNNING")
    return dispatch_login_job(db, organization_id, actor_id, job_id)


def recover_stale_sessions(db: Session, organization_id: str, actor_id: str, stale_after_seconds: int = 900) -> int:
    cutoff = utcnow().timestamp() - stale_after_seconds
    sessions = db.scalars(select(BrowserSession).options(joinedload(BrowserSession.profile)).where(BrowserSession.organization_id == organization_id, BrowserSession.state.in_(ACTIVE_SESSION_STATES))).all()
    recovered = 0
    for session in sessions:
        if session.last_event_at.timestamp() >= cutoff:
            continue
        close_local_browser(session.id)
        session.state = "NEEDS_ATTENTION"
        session.login_state = "STALE_WORKER"
        session.failure_reason = "The session stopped reporting heartbeats"
        session.last_event_at = utcnow()
        session.profile.status = "RESERVED"
        session.profile.lock_state = "UNLOCKED"
        session.profile.active_session_id = None
        session.profile.last_closed_at = utcnow()
        _sync_job_item_for_session(db, session.id, session.state)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.stale_recovered", target_type="browser_session", target_id=session.id, result="attention", metadata_json={"account_id": session.account_id, "stale_after_seconds": stale_after_seconds}))
        recovered += 1
    if recovered:
        db.commit()
    return recovered


def _sync_job_item_for_session(db: Session, session_id: str, session_state: str) -> None:
    item = db.scalar(select(LoginJobItem).where(LoginJobItem.session_id == session_id))
    if not item or item.state != "RUNNING":
        return
    job = db.get(LoginJob, item.job_id)
    item.state = "SUCCEEDED" if session_state == "RUNNING" else "ATTENTION"
    if job:
        job.running_count = max(0, job.running_count - 1)
        if item.state == "SUCCEEDED":
            job.succeeded_count += 1
        else:
            job.attention_count += 1
        if job.queued_count == 0 and job.running_count == 0:
            job.status = "COMPLETED"
            job.completed_at = utcnow()


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
        "session_url": f"/session/{session.id}",
        "opened_at": session.opened_at,
        "last_event_at": session.last_event_at,
        "closed_at": session.closed_at,
        "failure_reason": session.failure_reason,
    }


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
    profile = _ensure_browser_profile(db, account)
    if profile.active_session_id:
        active = db.scalar(select(BrowserSession).where(BrowserSession.id == profile.active_session_id, BrowserSession.state.in_(ACTIVE_SESSION_STATES)))
        if active:
            raise ImportValidationError("This account already has an open login session")
    session = BrowserSession(id=_new_id("session"), organization_id=organization_id, account_id=account.id, profile_id=profile.id, state="LOGIN_REQUIRED", login_state="REQUIRED", runtime_type="LOCAL_HANDOFF", metadata_json={"boundary": "human_login"})
    profile.status = "OPEN"
    profile.lock_state = "LOCKED"
    profile.active_session_id = session.id
    profile.last_opened_at = utcnow()
    account.login_status = "REQUIRED"
    db.add(session)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.started", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account.id, "profile_id": profile.id, "runtime": "LOCAL_HANDOFF"}))
    db.commit()
    launched, process_id = launch_local_browser(profile.id, session.id)
    if launched:
        session.runtime_type = "LOCAL_CHROME"
        session.metadata_json = {"boundary": "human_login", "browser_opened": True, "process_id": process_id}
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.browser_opened", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account.id, "profile_id": profile.id, "runtime": "LOCAL_CHROME"}))
        db.commit()
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
        # Keep the persisted reason controlled by the service. Never allow an
        # event payload to become an audit/session log line that could contain
        # imported credential material.
        session.failure_reason = "Google requested additional verification"
    elif event_type == "UNKNOWN_PAGE":
        session.state = "NEEDS_ATTENTION"
        session.login_state = "UNKNOWN_PAGE"
        session.failure_reason = "The worker encountered an unrecognized page"
    elif event_type == "HEARTBEAT":
        pass
    if event_type in {"LOGIN_COMPLETED", "VERIFICATION_REQUIRED", "UNKNOWN_PAGE"}:
        _sync_job_item_for_session(db, session.id, session.state)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action=f"session.{event_type.lower()}", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": session.account_id}))
    db.commit()
    return get_session(db, organization_id, session.id)


def close_session(db: Session, organization_id: str, actor_id: str, session_id: str) -> BrowserSession:
    session = get_session(db, organization_id, session_id)
    if not session:
        raise ImportValidationError("Session not found")
    if session.state == "CLOSED":
        return session
    close_local_browser(session.id)
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
    _sync_job_item_for_session(db, session.id, session.state)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.closed", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": session.account_id, "profile_id": session.profile_id}))
    db.commit()
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
        updated = record_session_event(db, organization_id, actor_id, session_id, "VERIFICATION_REQUIRED")
        return signal.value, action.value, updated
    if signal == "UNKNOWN":
        updated = record_session_event(db, organization_id, actor_id, session_id, "UNKNOWN_PAGE")
        return signal.value, action.value, updated
    session.last_event_at = utcnow()
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="session.page_observed", target_type="browser_session", target_id=session.id, result="success", metadata_json=metadata))
    db.commit()
    return signal.value, action.value, get_session(db, organization_id, session.id)
