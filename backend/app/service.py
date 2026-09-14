import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from .models import AccountGroup, AccountSecret, AuditEvent, GmailAccount, ImportOperation
from .schemas import AccountStructuredInput, BulkPreviewResponse, PreviewRow
from .security import SecretCipher, mask_recovery

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
        db.add(AccountSecret(id=_new_id("secret"), account_id=account_id, secret_ref=f"secret://account/{account_id}/primary", encrypted_payload=cipher.encrypt({"password": line.password, "recovery": line.recovery, "2fa": line.two_fa})))
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
