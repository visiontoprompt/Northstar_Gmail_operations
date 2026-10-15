import os
import re
import socket
import uuid
import base64
import json
import time
import secrets
import threading
from urllib.request import ProxyHandler, Request, build_opener
from dataclasses import dataclass
from datetime import timedelta, timezone
from logging import getLogger
from pathlib import Path

from sqlalchemy import func, nullslast, or_, select, update
from sqlalchemy.orm import Session, joinedload, selectinload

from .config import get_settings
from .contacts import build_google_contacts_csv, contact_file_checksum, new_contact_file_id, parse_emails, validate_contact_file, write_contact_file, write_contacts_upload
from .addons import select_addon_candidates
from .alerts import choose_alert_topics
from .db import SessionLocal
from .models import AccountGroup, AccountSecret, AuditEvent, BrowserProfile, BrowserSession, ContactFile, CredentialChangePlan, CredentialRevision, Employee, EmployeeGroupAccess, GmailAccount, ImportOperation, JobLock, LoginJob, LoginJobItem, Newsletter, NewsletterAccountIdentity, NewsletterAutomationProfile, NewsletterAutomationSkill, NewsletterSubscription, OperationDraft, Organization, ProxyGroup, ProxyInventory, WorkspaceSettings, utcnow
from .schemas import NEWSLETTER_SIGNABLE_PROFILE_STATUSES, AccountStructuredInput, BulkPreviewResponse, PreviewRow
from .security import SecretCipher, mask_recovery
from .notifications import notification_settings, publish, safe_notification_settings
from .runtime import close_local_browser, launch_local_browser, local_browser_is_alive, local_browser_profiles_alive, remove_local_profile, resolve_browser_executable
from .observer import BrowserObserver
from .newsletter_discovery import NewsletterDiscoveryError, validate_newsletter_runtime_url
from .newsletter_execution import AGENT_LEARNING, DETERMINISTIC, LIVE_FORM, MANUAL, SKILL_REPLAY, choose_newsletter_execution_mode
from .newsletter_identity import build_newsletter_identity, merge_newsletter_identity
from .newsletter_agent import normalize_newsletter_agent_chain
from .login_policy import PageObservation, PageSignal, classify_page, is_totp_challenge_url
from .proxy import ProxyValidationError, parse_proxy, proxy_from_payload
from .worker.locks import RedisLocks, account_lock_key
from .worker.queue import RedisQueue
from urllib.parse import urlparse


def _newsletter_models() -> tuple[type, type]:
    """Late import for the newsletter models.

    ``newsletter_service`` imports this module, so importing the newsletter
    tables at module scope here would be a circular import.
    """
    from .models import NewsletterJob, NewsletterTask

    return NewsletterJob, NewsletterTask

logger = getLogger(__name__)

WORKER_PREFIX = "worker-"
API_WORKER_ID = "orchestrator-api"
OPERATION_TYPES = {"contacts", "security", "labels", "apps", "mail", "addons", "alerts", "preferences"}
FORBIDDEN_OPERATION_KEY_PARTS = ("password", "recovery", "2fa", "totp", "cookie", "token", "secret")
CREDENTIAL_CHANGE_ACTIVE_STATES = ("DRAFT", "READY_FOR_OPERATOR", "GOOGLE_CHANGE_CONFIRMED")
CREDENTIAL_CHANGE_TERMINAL_STATES = ("STORED_ENCRYPTED", "FAILED", "CANCELLED")
MAIL_REPLY_ROTATION_POOL = (
    "Thank you for reaching out. I’m happy to confirm that we accept your proposal. Please share the next steps and any information you need from us.",
    "Hello, thank you for your message. I hope you’re doing well. I’m pleased to connect and will be happy to help.",
    "Salut, merci pour ton message. J’espère que tu vas bien. Je reviens vers toi rapidement avec les prochaines informations.",
    "Thank you for considering us. After reviewing the request, we’re unable to proceed at this time. We appreciate your understanding and wish you every success.",
    "Thank you very much for your message and for the information provided. We appreciate your time and consideration.",
    "Thank you for sending this through. I confirm that we received your message and will review it carefully before coming back to you.",
    "Thank you for your email. We’re reviewing the details and will follow up with you as soon as we have a complete update.",
    "Thank you for your message. Could you please provide a little more detail about the request and the outcome you’re looking for?",
    "Thank you for the update. I confirm that this has been reviewed and approved. Please proceed with the next step when ready.",
    "Thank you for reaching out. I’d be glad to discuss this further. Please send a few suitable times, and I’ll confirm a meeting slot.",
    "Thank you for your patience. I’m sorry for the delay and any inconvenience caused. We’re addressing this now and will keep you updated.",
    "Thank you again for getting in touch. Please let me know if you need anything else. I look forward to hearing from you.",
)
_UNSET = object()

# A persistent Chrome profile can only be owned by one observer at a time.
# Keep the reservation and the browser hand-off atomic inside this process so
# two rapid bulk clicks cannot both pass the profile check and then kill each
# other's Chrome instance during launch.
_ACCOUNT_START_LOCKS: dict[str, threading.Lock] = {}
_ACCOUNT_START_LOCKS_GUARD = threading.Lock()


def _account_start_lock(account_id: str) -> threading.Lock:
    with _ACCOUNT_START_LOCKS_GUARD:
        return _ACCOUNT_START_LOCKS.setdefault(account_id, threading.Lock())


def _worker_token() -> str:
    return f"{WORKER_PREFIX}{socket.gethostname()}_{os.getpid()}"

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ImportValidationError(ValueError):
    pass


class AccountSessionConflictError(ImportValidationError):
    """An account already has a browser session; callers must wait, not retry signup."""


class BrowserLaunchError(ImportValidationError):
    """Raised when the local Chrome process could not be started. Nothing is
    persisted for the session, so a later attempt can retry cleanly."""


def _group_payload(db: Session, group: AccountGroup) -> dict:
    account_count = db.scalar(select(func.count(GmailAccount.id)).where(GmailAccount.organization_id == group.organization_id, GmailAccount.group_id == group.id, GmailAccount.lifecycle_status != "ARCHIVED")) or 0
    return {"id": group.id, "name": group.name, "description": group.description, "capacity": group.capacity, "account_count": account_count, "ready_count": account_count, "issue_count": 0, "status": group.status}


def update_group(db: Session, organization_id: str, actor_id: str, group_id: str, name: str | None, description: str | None, capacity: int | None, group_status: str | None) -> dict:
    group = db.scalar(select(AccountGroup).where(AccountGroup.organization_id == organization_id, AccountGroup.id == group_id).with_for_update())
    if not group:
        raise ImportValidationError("Group not found")
    active_count = db.scalar(select(func.count(GmailAccount.id)).where(GmailAccount.organization_id == organization_id, GmailAccount.group_id == group.id, GmailAccount.lifecycle_status != "ARCHIVED")) or 0
    changes: dict[str, object] = {}
    if name is not None and name != group.name:
        duplicate = db.scalar(select(AccountGroup.id).where(AccountGroup.organization_id == organization_id, AccountGroup.name == name, AccountGroup.id != group.id))
        if duplicate:
            raise ImportValidationError("A group with this name already exists")
        group.name = name
        changes["name"] = name
    if description != group.description:
        group.description = description
        changes["description_changed"] = True
    if capacity is not None and capacity != group.capacity:
        if capacity < active_count:
            raise ImportValidationError(f"Capacity cannot be lower than the {active_count} active accounts in this group")
        group.capacity = capacity
        changes["capacity"] = capacity
    if group_status is not None and group_status != group.status:
        group.status = group_status
        changes["status"] = group_status
    if changes:
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="group.updated", target_type="account_group", target_id=group.id, result="success", metadata_json=changes))
        db.commit()
    return _group_payload(db, group)


def employee_response(employee: Employee) -> dict:
    return {"id": employee.id, "full_name": employee.full_name, "email": employee.email, "role": employee.role, "status": employee.status, "notes": employee.notes, "created_at": employee.created_at, "updated_at": employee.updated_at, "group_access": [{"group_id": access.group_id, "group_name": access.group.name, "access_level": access.access_level} for access in getattr(employee, "group_access", [])]}


def list_employees(db: Session, organization_id: str, query: str | None = None, employee_status: str | None = None) -> list[dict]:
    filters = [Employee.organization_id == organization_id]
    if query and query.strip():
        term = f"%{query.strip().lower()}%"
        filters.append(or_(Employee.full_name.ilike(term), Employee.email.ilike(term), Employee.role.ilike(term)))
    if employee_status:
        filters.append(Employee.status == employee_status)
    statement = select(Employee).options(selectinload(Employee.group_access).selectinload(EmployeeGroupAccess.group)).where(*filters).order_by(Employee.full_name, Employee.created_at)
    return [employee_response(row) for row in db.scalars(statement).all()]


def create_employee(db: Session, organization_id: str, actor_id: str, full_name: str, email: str, role: str, notes: str | None) -> dict:
    normalized_email = email.strip().lower()
    if db.scalar(select(Employee.id).where(Employee.organization_id == organization_id, Employee.email == normalized_email)):
        raise ImportValidationError("An employee with this email already exists")
    employee = Employee(id=_new_id("employee"), organization_id=organization_id, full_name=full_name.strip(), email=normalized_email, role=role, status="INVITED", notes=notes.strip() if notes else None)
    db.add(employee)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="employee.created", target_type="employee", target_id=employee.id, result="success", metadata_json={"email_domain": normalized_email.split("@", 1)[1], "role": role, "status": "INVITED"}))
    db.commit()
    db.refresh(employee)
    return employee_response(employee)


def update_employee(db: Session, organization_id: str, actor_id: str, employee_id: str, full_name: str | None, email: str | None, role: str | None, employee_status: str | None, notes: str | None | object = _UNSET) -> dict:
    employee = db.scalar(select(Employee).where(Employee.organization_id == organization_id, Employee.id == employee_id).with_for_update())
    if not employee:
        raise ImportValidationError("Employee not found")
    changes: dict[str, object] = {}
    normalized_email = email.strip().lower() if email is not None else None
    if normalized_email and normalized_email != employee.email:
        if db.scalar(select(Employee.id).where(Employee.organization_id == organization_id, Employee.email == normalized_email, Employee.id != employee.id)):
            raise ImportValidationError("An employee with this email already exists")
        employee.email = normalized_email
        changes["email_changed"] = True
    if full_name is not None and full_name != employee.full_name:
        employee.full_name = full_name.strip()
        changes["full_name_changed"] = True
    if role is not None and role != employee.role:
        employee.role = role
        changes["role"] = role
    if employee_status is not None and employee_status != employee.status:
        employee.status = employee_status
        changes["status"] = employee_status
    if notes is not _UNSET and notes != employee.notes:
        employee.notes = notes.strip() if isinstance(notes, str) and notes else None
        changes["notes_changed"] = True
    if changes:
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="employee.updated", target_type="employee", target_id=employee.id, result="success", metadata_json=changes))
        db.commit()
        db.refresh(employee)
    response = employee_response(employee)
    response["group_access"] = get_employee_group_access(db, organization_id, employee.id)
    return response


def get_employee_group_access(db: Session, organization_id: str, employee_id: str) -> list[dict]:
    employee = db.scalar(select(Employee).options(selectinload(Employee.group_access).selectinload(EmployeeGroupAccess.group)).where(Employee.organization_id == organization_id, Employee.id == employee_id))
    if not employee:
        raise ImportValidationError("Employee not found")
    return [{"group_id": access.group_id, "group_name": access.group.name, "access_level": access.access_level} for access in employee.group_access]


def set_employee_group_access(db: Session, organization_id: str, actor_id: str, employee_id: str, assignments: list[dict]) -> dict:
    employee = db.scalar(select(Employee).where(Employee.organization_id == organization_id, Employee.id == employee_id).with_for_update())
    if not employee:
        raise ImportValidationError("Employee not found")
    group_ids = [item["group_id"] for item in assignments]
    if len(group_ids) != len(set(group_ids)):
        raise ImportValidationError("Each group can be assigned only once")
    groups = db.scalars(select(AccountGroup).where(AccountGroup.organization_id == organization_id, AccountGroup.id.in_(group_ids))).all() if group_ids else []
    if len(groups) != len(group_ids):
        raise ImportValidationError("Choose valid groups in this organization")
    existing = db.scalars(select(EmployeeGroupAccess).where(EmployeeGroupAccess.organization_id == organization_id, EmployeeGroupAccess.employee_id == employee.id)).all()
    existing_by_group = {access.group_id: access for access in existing}
    submitted_ids = set(group_ids)
    for access in existing:
        if access.group_id not in submitted_ids:
            db.delete(access)
    for item in assignments:
        access = existing_by_group.get(item["group_id"])
        if access is None:
            db.add(EmployeeGroupAccess(id=_new_id("egaccess"), organization_id=organization_id, employee_id=employee.id, group_id=item["group_id"], access_level=item["access_level"]))
        elif access.access_level != item["access_level"]:
            access.access_level = item["access_level"]
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="employee.group_access.updated", target_type="employee", target_id=employee.id, result="success", metadata_json={"group_count": len(assignments), "group_ids": group_ids, "access_levels": {item["group_id"]: item["access_level"] for item in assignments}}))
    db.commit()
    refreshed = db.scalar(select(Employee).options(selectinload(Employee.group_access).selectinload(EmployeeGroupAccess.group)).where(Employee.organization_id == organization_id, Employee.id == employee.id))
    if not refreshed:
        raise ImportValidationError("Employee not found")
    group_names = {group.id: group.name for group in groups}
    response = employee_response(refreshed)
    response["group_access"] = [{"group_id": item["group_id"], "group_name": group_names[item["group_id"]], "access_level": item["access_level"]} for item in assignments]
    return response


def get_workspace_settings(db: Session, organization_id: str) -> dict:
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise ImportValidationError("Organization not found")
    settings_row = db.scalar(select(WorkspaceSettings).where(WorkspaceSettings.organization_id == organization_id))
    if settings_row is None:
        settings_row = WorkspaceSettings(id=_new_id("settings"), organization_id=organization_id)
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    runtime = get_settings()
    notification = safe_notification_settings(notification_settings(db, organization_id))
    return {"id": settings_row.id, "organization_id": organization_id, "organization_name": organization.name, "default_group_capacity": settings_row.default_group_capacity, "default_login_concurrency": settings_row.default_login_concurrency, "max_concurrent_operations": settings_row.max_concurrent_operations, "max_active_profiles": settings_row.max_active_profiles, "observer_interval_ms": settings_row.observer_interval_ms, "session_idle_timeout_minutes": settings_row.session_idle_timeout_minutes, "auto_close_completed_sessions": settings_row.auto_close_completed_sessions, "retain_audit_days": settings_row.retain_audit_days, "newsletter_agent_enabled": settings_row.newsletter_agent_enabled, "newsletter_agent_provider": settings_row.newsletter_agent_provider, "newsletter_agent_model": settings_row.newsletter_agent_model, "newsletter_agent_temperature": settings_row.newsletter_agent_temperature, "newsletter_agent_max_steps": settings_row.newsletter_agent_max_steps, "newsletter_agent_timeout_seconds": settings_row.newsletter_agent_timeout_seconds, "newsletter_agent_shadow_mode": settings_row.newsletter_agent_shadow_mode, "newsletter_agent_live_execution_enabled": settings_row.newsletter_agent_live_execution_enabled, "newsletter_agent_learning_mode": settings_row.newsletter_agent_learning_mode, "newsletter_agent_primary_model": settings_row.newsletter_agent_primary_model, "newsletter_agent_secondary_model": settings_row.newsletter_agent_secondary_model, "newsletter_agent_free_router_model": settings_row.newsletter_agent_free_router_model, "newsletter_agent_max_ai_calls": settings_row.newsletter_agent_max_ai_calls, "newsletter_agent_min_confidence": settings_row.newsletter_agent_min_confidence, "newsletter_agent_vision_enabled": settings_row.newsletter_agent_vision_enabled, "newsletter_agent_vision_max_calls": settings_row.newsletter_agent_vision_max_calls, "newsletter_agent_skill_replay_enabled": settings_row.newsletter_agent_skill_replay_enabled, "newsletter_agent_deterministic_resolver_enabled": settings_row.newsletter_agent_deterministic_resolver_enabled, "newsletter_agent_automatic_relearning_enabled": settings_row.newsletter_agent_automatic_relearning_enabled, "newsletter_agent_chain": normalize_newsletter_agent_chain(getattr(settings_row, "newsletter_agent_chain", None)), "openrouter_api_key_configured": bool(settings_row.openrouter_api_key_encrypted), "openai_api_key_configured": bool(getattr(settings_row, "openai_api_key_encrypted", None)), "two_captcha_api_key_configured": bool(settings_row.two_captcha_api_key_encrypted), "two_captcha_enabled": settings_row.two_captcha_enabled, "ovh_endpoint": settings_row.ovh_endpoint, "ovh_credentials_configured": bool(settings_row.ovh_credentials_encrypted), "runtime_environment": runtime.environment, "runtime_browser_launch_enabled": runtime.browser_launch_enabled, "runtime_observer_enabled": runtime.browser_observer_enabled, "runtime_worker_enabled": runtime.worker_enabled, "runtime_redis_configured": bool(runtime.redis_url), "updated_at": settings_row.updated_at, **notification}


def update_workspace_settings(db: Session, organization_id: str, actor_id: str, values: dict[str, object]) -> dict:
    get_workspace_settings(db, organization_id)
    settings_row = db.scalar(select(WorkspaceSettings).where(WorkspaceSettings.organization_id == organization_id).with_for_update())
    if settings_row is None:
        raise ImportValidationError("Workspace settings could not be initialized")
    changed = {}
    secret_value = values.pop("openrouter_api_key", None)
    openai_secret_value = values.pop("openai_api_key", None)
    captcha_secret_value = values.pop("two_captcha_api_key", None)
    ovh_parts = {key: values.pop(key, None) for key in ("ovh_application_key", "ovh_application_secret", "ovh_consumer_key")}
    clear_ovh = values.pop("ovh_clear_credentials", False)
    if clear_ovh and any(ovh_parts.values()):
        raise ImportValidationError("Clear OVH credentials or provide new credentials, not both")
    if any(value is not None for value in ovh_parts.values()) and not all(ovh_parts.values()):
        raise ImportValidationError("Enter all three OVH API credentials together")
    if clear_ovh:
        settings_row.ovh_credentials_encrypted = None
        changed["ovh_credentials"] = "cleared"
    elif all(ovh_parts.values()):
        settings_row.ovh_credentials_encrypted = SecretCipher().encrypt(ovh_parts)
        changed["ovh_credentials"] = "configured"
    if "newsletter_agent_chain" in values:
        values["newsletter_agent_chain"] = normalize_newsletter_agent_chain(values["newsletter_agent_chain"])
    if secret_value is not None:
        settings_row.openrouter_api_key_encrypted = SecretCipher().encrypt({"value": secret_value}) if secret_value else None
        changed["openrouter_api_key"] = "configured" if secret_value else "cleared"
    if openai_secret_value is not None:
        settings_row.openai_api_key_encrypted = SecretCipher().encrypt({"value": openai_secret_value}) if openai_secret_value else None
        changed["openai_api_key"] = "configured" if openai_secret_value else "cleared"
    if captcha_secret_value is not None:
        settings_row.two_captcha_api_key_encrypted = SecretCipher().encrypt({"value": captcha_secret_value}) if captcha_secret_value else None
        changed["two_captcha_api_key"] = "configured" if captcha_secret_value else "cleared"
    for key, value in values.items():
        if value is not None and hasattr(settings_row, key) and getattr(settings_row, key) != value:
            setattr(settings_row, key, value)
            changed[key] = value
    if changed:
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="workspace_settings.updated", target_type="workspace_settings", target_id=settings_row.id, result="success", metadata_json=changed))
        db.commit()
    return get_workspace_settings(db, organization_id)


def _newsletter_agent_runtime_settings(db: Session, organization_id: str) -> dict[str, object]:
    """Resolve organization-scoped agent settings without exposing the key."""
    row = db.scalar(select(WorkspaceSettings).where(WorkspaceSettings.organization_id == organization_id))
    if row is None:
        return {"enabled": False, "configured_enabled": False, "api_key_configured": False}
    api_key = None
    if row.openrouter_api_key_encrypted:
        try:
            api_key = SecretCipher().decrypt(row.openrouter_api_key_encrypted).get("value")
        except Exception:  # noqa: BLE001 - invalidated secrets disable fallback safely
            logger.warning("Unable to decrypt the workspace OpenRouter key")
    openai_api_key = None
    if getattr(row, "openai_api_key_encrypted", None):
        try:
            openai_api_key = SecretCipher().decrypt(row.openai_api_key_encrypted).get("value")
        except Exception:  # noqa: BLE001 - invalidated secrets disable that provider safely
            logger.warning("Unable to decrypt the workspace OpenAI key")
    provider_keys = {"openrouter": bool(api_key), "openai": bool(openai_api_key)}
    return {
        "enabled": bool(row.newsletter_agent_enabled and any(provider_keys.values())),
        "configured_enabled": bool(row.newsletter_agent_enabled),
        "api_key_configured": any(provider_keys.values()),
        "provider_keys": provider_keys,
        "provider": row.newsletter_agent_provider,
        "model": row.newsletter_agent_model,
        "temperature": row.newsletter_agent_temperature,
        "max_steps": row.newsletter_agent_max_steps,
        "timeout_seconds": row.newsletter_agent_timeout_seconds,
        "shadow_mode": row.newsletter_agent_shadow_mode,
        "live_execution_enabled": row.newsletter_agent_live_execution_enabled,
        "learning_mode": row.newsletter_agent_learning_mode,
        "primary_model": row.newsletter_agent_primary_model,
        "secondary_model": row.newsletter_agent_secondary_model,
        "free_router_model": row.newsletter_agent_free_router_model,
        "max_ai_calls": row.newsletter_agent_max_ai_calls,
        "min_confidence": row.newsletter_agent_min_confidence,
        "vision_enabled": row.newsletter_agent_vision_enabled,
        "vision_max_calls": row.newsletter_agent_vision_max_calls,
        "skill_replay_enabled": row.newsletter_agent_skill_replay_enabled,
        "deterministic_resolver_enabled": row.newsletter_agent_deterministic_resolver_enabled,
        "automatic_relearning_enabled": row.newsletter_agent_automatic_relearning_enabled,
        "agent_chain": normalize_newsletter_agent_chain(getattr(row, "newsletter_agent_chain", None)),
        "api_key": api_key,
        "openai_api_key": openai_api_key,
        # The optional browser-use resolver reads this sub-dict. It needs the
        # workspace switch and an OpenAI key here, and still honours the agent's
        # shadow and live-execution gates. Whether the browser-use package is
        # actually importable is checked at call time, not while assembling
        # settings, so this path never imports the optional dependency.
        "browser_use_settings": {
            "browser_use_enabled": bool(get_settings().newsletter_browser_use_enabled and openai_api_key),
            "live_execution_enabled": bool(row.newsletter_agent_live_execution_enabled),
            "shadow_mode": bool(row.newsletter_agent_shadow_mode),
            "api_key": openai_api_key,
            "model": get_settings().newsletter_browser_use_model,
            "timeout_seconds": get_settings().newsletter_browser_use_timeout_seconds,
            "min_confidence": row.newsletter_agent_min_confidence,
        },
    }


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
        if len(parts) not in (2, 3, 4) or any(not part.strip() for part in parts):
            rows.append(PreviewRow(row_number=row_number, valid=False, error="Use email:password, email:password:recovery, or email:password:recovery:2fa"))
            continue
        email, password = (part.strip() for part in parts[:2])
        recovery = parts[2].strip() if len(parts) >= 3 else ""
        two_fa = parts[3].strip() if len(parts) == 4 else ""
        normalized_email = email.lower()
        if not EMAIL_RE.match(normalized_email):
            rows.append(PreviewRow(row_number=row_number, valid=False, error="Enter a valid email address"))
            continue
        parsed.append(ParsedLine(row_number, normalized_email, password, recovery, two_fa))
        rows.append(PreviewRow(row_number=row_number, email=normalized_email, recovery_masked=mask_recovery(recovery) if recovery else None, two_fa_configured=bool(two_fa), valid=True))
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
    account = GmailAccount(id=account_id, organization_id=organization_id, group_id=group.id, email=email, normalized_email=email, lifecycle_status="SETUP_REQUIRED", readiness_status="READY_FOR_LOGIN", recovery_hint=mask_recovery(data.recovery) if data.recovery else "Not provided", two_fa_configured=bool(data.two_fa), proxy_id=proxy.proxy_id if proxy else None, proxy_label=data.proxy_label or (proxy.display_endpoint if proxy else None), proxy_region=data.proxy_region, proxy_status="ASSIGNED" if proxy or data.proxy_label or data.proxy_region else "UNASSIGNED", profile_id=profile_folder_name(organization_id, email), profile_storage_ref=f"profiles/{organization_id}/{account_id}", profile_status="RESERVED")
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
        account = GmailAccount(id=account_id, organization_id=organization_id, group_id=group_id, email=line.email, normalized_email=line.email, lifecycle_status="SETUP_REQUIRED", readiness_status="READY_FOR_LOGIN", recovery_hint=mask_recovery(line.recovery) if line.recovery else "Not provided", two_fa_configured=bool(line.two_fa), login_status="NOT_STARTED", gmail_status="NOT_CHECKED", proxy_status="UNASSIGNED", profile_id=profile_folder_name(organization_id, line.email), profile_storage_ref=f"profiles/{organization_id}/{account_id}", profile_status="RESERVED")
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
    proxy_endpoint = None
    if account.secret and account.proxy_id:
        try:
            proxy_config = proxy_from_payload(_safe_secret_payload(account).get("proxy"))
            proxy_endpoint = proxy_config.display_endpoint if proxy_config else None
        except ImportValidationError:
            # Keep account records available if a legacy/corrupt vault cannot
            # be opened; the safe label remains visible as the fallback.
            pass
    return {"id": account.id, "email": account.email, "group_id": account.group_id, "group_name": account.group.name, "lifecycle_status": account.lifecycle_status, "readiness_status": account.readiness_status, "login_status": login_status, "gmail_status": account.gmail_status, "secret_status": "STORED_ENCRYPTED" if account.secret else "MISSING", "recovery_masked": account.recovery_hint, "two_fa_configured": account.two_fa_configured, "proxy": {"id": account.proxy_id, "label": account.proxy_label, "endpoint": proxy_endpoint, "region": account.proxy_region, "status": account.proxy_status}, "tags": account.tags or [], "profile": {"id": account.profile_id, "storage_reference": account.profile_storage_ref, "status": profile_status}, "created_at": account.created_at, "updated_at": account.updated_at}


def list_accounts(db: Session, organization_id: str, q: str | None, group_id: str | None, status: str | None, page: int, page_size: int, visible_group_ids: set[str] | None = None) -> dict:
    filters = [GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED", GmailAccount.account_type != "EXTERNAL_RECIPIENT"]
    if visible_group_ids is not None:
        filters.append(GmailAccount.group_id.in_(visible_group_ids or {"__no_access__"}))
    if q:
        term = f"%{q.strip().lower()}%"
        filters.append(or_(GmailAccount.email.ilike(term), AccountGroup.name.ilike(term)))
    if group_id:
        filters.append(GmailAccount.group_id == group_id)
    scope = list(filters)
    if status:
        if status == "AUTHENTICATED":
            filters.append(or_(GmailAccount.login_status == "AUTHENTICATED", (GmailAccount.login_status.in_(["NOT_STARTED", "REQUIRED"]) & (GmailAccount.profile_status == "SAVED"))))
        elif status == "NONEXISTENT":
            filters.append(GmailAccount.login_status.in_(["NONEXISTENT", "ACCOUNT_NOT_FOUND"]))
        elif status == "VERIFICATION_REQUIRED":
            filters.append(GmailAccount.login_status == "VERIFICATION_REQUIRED")
        elif status == "NEVER_LOGGED_IN":
            filters.append(GmailAccount.login_status == "NOT_STARTED")
        elif status == "NEEDS_ATTENTION":
            filters.append(or_(GmailAccount.readiness_status == "SETUP_ERROR", GmailAccount.login_status.in_(["NONEXISTENT", "ACCOUNT_NOT_FOUND", "VERIFICATION_REQUIRED", "NEEDS_ATTENTION", "UNKNOWN_PAGE"])))
        elif status in {"NOT_STARTED", "REQUIRED", "ACCOUNT_NOT_FOUND", "VERIFICATION_REQUIRED", "UNKNOWN_PAGE"}:
            filters.append(GmailAccount.login_status == status)
        else:
            filters.append(GmailAccount.readiness_status == status)
    count_for = lambda extra: int(db.scalar(select(func.count(GmailAccount.id)).join(AccountGroup).where(*scope, *extra)) or 0)
    authenticated = or_(GmailAccount.login_status == "AUTHENTICATED", (GmailAccount.login_status.in_(["NOT_STARTED", "REQUIRED"]) & (GmailAccount.profile_status == "SAVED")))
    attention = or_(GmailAccount.readiness_status == "SETUP_ERROR", GmailAccount.login_status.in_(["NONEXISTENT", "ACCOUNT_NOT_FOUND", "VERIFICATION_REQUIRED", "NEEDS_ATTENTION", "UNKNOWN_PAGE"]))
    status_counts = {
        "ALL": count_for([]),
        "READY_FOR_LOGIN": count_for([GmailAccount.readiness_status == "READY_FOR_LOGIN"]),
        "SETUP_REQUIRED": count_for([GmailAccount.readiness_status == "SETUP_REQUIRED"]),
        "AUTHENTICATED": count_for([authenticated]),
        "NEEDS_ATTENTION": count_for([attention]),
        "NONEXISTENT": count_for([GmailAccount.login_status.in_(["NONEXISTENT", "ACCOUNT_NOT_FOUND"])]),
        "VERIFICATION_REQUIRED": count_for([GmailAccount.login_status == "VERIFICATION_REQUIRED"]),
        "NEVER_LOGGED_IN": count_for([GmailAccount.login_status == "NOT_STARTED"]),
    }
    total = db.scalar(select(func.count(GmailAccount.id)).join(AccountGroup).where(*filters)) or 0
    items = db.scalars(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).join(AccountGroup).where(*filters).order_by(GmailAccount.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": [account_response(item) for item in items], "page": page, "page_size": page_size, "total": total, "status_counts": status_counts}


def update_account_tags(db: Session, organization_id: str, actor_id: str, account_id: str, tags: list[str]) -> dict:
    account = get_account(db, organization_id, account_id)
    if not account or account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Account not found")
    account.tags = tags
    account.updated_at = utcnow()
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.tags_updated", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"tags": tags}))
    db.commit()
    return account_response(get_account(db, organization_id, account_id))


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


def get_newsletter_account_identity(db: Session, organization_id: str, account_id: str) -> dict:
    identity = db.scalar(select(NewsletterAccountIdentity).where(NewsletterAccountIdentity.organization_id == organization_id, NewsletterAccountIdentity.account_id == account_id))
    try:
        values = SecretCipher().decrypt(identity.encrypted_payload) if identity else {}
    except Exception:  # noqa: BLE001
        logger.warning("could not decrypt newsletter identity for account %s", account_id)
        values = {}
    return {"account_id": account_id, "values": values if isinstance(values, dict) else {}, "updated_at": identity.updated_at if identity else None}


def update_newsletter_account_identity(db: Session, organization_id: str, actor_id: str, account_id: str, values: dict[str, str]) -> dict:
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account:
        raise ImportValidationError("Account not found")
    identity = db.scalar(select(NewsletterAccountIdentity).where(NewsletterAccountIdentity.organization_id == organization_id, NewsletterAccountIdentity.account_id == account_id))
    if identity is None:
        identity = NewsletterAccountIdentity(id=_new_id("nlidentity"), organization_id=organization_id, account_id=account_id, encrypted_payload=SecretCipher().encrypt(values))
        db.add(identity)
    else:
        identity.encrypted_payload = SecretCipher().encrypt(values)
        identity.updated_at = utcnow()
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="newsletter.account_identity.updated", target_type="gmail_account", target_id=account_id, result="success", metadata_json={"field_count": len(values)}))
    db.commit()
    return get_newsletter_account_identity(db, organization_id, account_id)


def generate_newsletter_account_identity(
    db: Session,
    organization_id: str,
    actor_id: str,
    account_id: str,
    commit: bool = False,
    overrides: dict[str, str] | None = None,
) -> dict:
    """Preview or commit the generated placeholder profile for one account.

    The generated values are a base layer only: anything already saved for the
    account, and anything passed as an override, wins. Committing therefore
    never destroys an operator-approved value.
    """
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account:
        raise ImportValidationError("Account not found")
    organization = db.scalar(select(Organization).where(Organization.id == organization_id))
    generated = build_newsletter_identity(
        organization_id,
        account_id,
        org_name=getattr(organization, "name", None),
        account_email=account.email,
    )
    saved = get_newsletter_account_identity(db, organization_id, account_id).get("values") or {}
    merged = merge_newsletter_identity(generated, saved, overrides)
    if commit:
        result = update_newsletter_account_identity(db, organization_id, actor_id, account_id, merged)
    else:
        result = get_newsletter_account_identity(db, organization_id, account_id)
    return {
        "account_id": account_id,
        "values": merged,
        "generated_keys": sorted(key for key in generated if key not in saved and key not in (overrides or {})),
        "committed": bool(commit),
        "updated_at": result.get("updated_at"),
    }


def _publisher_signup_password(subscription: NewsletterSubscription, supplied: str | None = None) -> str | None:
    """Store and read the write-only credential for one account/site pair."""
    if supplied is not None:
        subscription.signup_password_encrypted = SecretCipher().encrypt({"password": supplied})
    if not subscription.signup_password_encrypted:
        return None
    try:
        payload = SecretCipher().decrypt(subscription.signup_password_encrypted)
        return str(payload.get("password") or "") or None
    except Exception as exc:  # noqa: BLE001 - never launch with an unreadable secret
        raise ImportValidationError("The saved publisher signup password could not be read") from exc


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


def _account_preferences_folder(account: GmailAccount) -> Path:
    profile_id = account.browser_profile.id if account.browser_profile else account.profile_id
    if not profile_id:
        raise ImportValidationError("This account does not have a browser profile yet")
    return (Path(get_settings().profile_root) / profile_id / "account-preferences").resolve()


def _account_preferences_payload(db: Session, organization_id: str, account_id: str) -> dict:
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account or account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Account not found")
    draft = db.scalar(select(OperationDraft).where(OperationDraft.organization_id == organization_id, OperationDraft.account_id == account_id, OperationDraft.operation_type == "preferences").order_by(OperationDraft.version.desc()).limit(1))
    config = dict(draft.configuration_json or {}) if draft else {}
    folder = _account_preferences_folder(account)
    filename = str(config.get("avatar_filename") or "").strip() or None
    avatar_available = bool(filename and (folder / filename).is_file())
    return {"account_id": account.id, "email": account.email, "gmail_theme": str(config.get("gmail_theme") or "default"), "avatar_filename": filename if avatar_available else None, "avatar_available": avatar_available, "updated_at": draft.updated_at if draft else None}


def get_account_preferences(db: Session, organization_id: str, account_id: str) -> dict:
    return _account_preferences_payload(db, organization_id, account_id)


def update_account_preferences(db: Session, organization_id: str, actor_id: str, account_id: str, gmail_theme: str, avatar_filename: str | None) -> dict:
    if gmail_theme not in {"default", "dark", "light", "high_contrast"}:
        raise ImportValidationError("Choose a supported Gmail theme")
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account or account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Account not found")
    normalized_filename = str(avatar_filename or "").strip() or None
    if normalized_filename:
        if Path(normalized_filename).name != normalized_filename or not re.fullmatch(r"avatar\.(?:png|jpe?g|webp|gif)", normalized_filename, re.IGNORECASE):
            raise ImportValidationError("Choose an uploaded PNG, JPEG, WEBP, or GIF image")
        if not (_account_preferences_folder(account) / normalized_filename).is_file():
            raise ImportValidationError("Upload the account image before saving preferences")
    create_operation_draft(db, organization_id, actor_id, account_id, "preferences", {"gmail_theme": gmail_theme, **({"avatar_filename": normalized_filename} if normalized_filename else {})})
    return _account_preferences_payload(db, organization_id, account_id)


def upload_account_preference_avatar(db: Session, organization_id: str, actor_id: str, account_id: str, filename: str, content: bytes) -> dict:
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account or account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Account not found")
    if not content or len(content) > 5 * 1024 * 1024:
        raise ImportValidationError("Account images must be between 1 byte and 5 MB")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ImportValidationError("Use a PNG, JPEG, WEBP, or GIF image")
    folder = _account_preferences_folder(account)
    existing_draft = db.scalar(select(OperationDraft).where(OperationDraft.organization_id == organization_id, OperationDraft.account_id == account_id, OperationDraft.operation_type == "preferences").order_by(OperationDraft.version.desc()).limit(1))
    existing_theme = str((existing_draft.configuration_json or {}).get("gmail_theme") or "default") if existing_draft else "default"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("avatar.*"):
        old.unlink(missing_ok=True)
    stored_name = f"avatar{suffix}"
    (folder / stored_name).write_bytes(content)
    create_operation_draft(db, organization_id, actor_id, account_id, "preferences", {"gmail_theme": existing_theme, "avatar_filename": stored_name})
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account_preferences.avatar_uploaded", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"filename": stored_name, "size_bytes": len(content)}))
    db.commit()
    return _account_preferences_payload(db, organization_id, account.id)


def update_credentials(db: Session, organization_id: str, actor_id: str, account_id: str, password: str | None, recovery: str | None, two_fa: str | None) -> dict:
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
        if isinstance(existing_payload, dict):
            for key in ("password", "recovery", "2fa"):
                if payload[key] is None:
                    payload[key] = existing_payload.get(key)
        if isinstance(existing_payload, dict) and existing_payload.get("proxy"):
            payload["proxy"] = existing_payload["proxy"]
    if any(not isinstance(payload[key], str) or not payload[key].strip() for key in ("password", "recovery", "2fa")):
        raise ImportValidationError("The existing encrypted credential set is incomplete; provide all credential fields")
    encrypted_payload = SecretCipher().encrypt(payload)
    if account.secret:
        account.secret.encrypted_payload = encrypted_payload
        account.secret.key_version = "v1"
    else:
        account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", encrypted_payload=encrypted_payload)
    account.recovery_hint = mask_recovery(payload["recovery"])
    account.two_fa_configured = bool(payload["2fa"])
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


def _new_totp_secret() -> str:
    """Generate a fresh RFC 4648 base32 authenticator secret for server-side
    provisioning. Returned to the operator exactly once on the confirm
    response so they can configure their authenticator app; it is then only
    ever stored encrypted inside the account vault."""
    return base64.b32encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")


def _generate_password() -> str:
    """Generate a fresh password that satisfies Google's account password rules
    (at least 8 characters, mixed case, digits and symbols)."""
    upper = secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    lower = secrets.choice("abcdefghijklmnopqrstuvwxyz")
    digit = secrets.choice("23456789")
    symbol = secrets.choice("!@#$%^&*")
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ23456789!@#$%^&*"
    rest = "".join(secrets.choice(alphabet) for _ in range(16))
    parts = [upper, lower, digit, symbol, rest]
    secrets.SystemRandom().shuffle(parts)
    return "".join(parts)


def _rotate_account_credentials_in_db(db: Session, account: GmailAccount, payload: dict, actor_id: str) -> int:
    """Encrypt and store a credential payload on the account vault, retiring the
    previous active revision. Shared by the operator confirm step and the
    automatic save that runs when the observer reports a completed change; the
    unspecified fields are backfilled from the existing vault, so a plan that
    rotates a single field never blanks the others. Returns the new revision
    version."""
    existing: dict = {}
    if account.secret:
        try:
            decrypted = SecretCipher().decrypt(account.secret.encrypted_payload) or {}
        except Exception as exc:  # noqa: BLE001
            raise ImportValidationError("The encrypted account vault could not be opened") from exc
        if isinstance(decrypted, dict):
            existing = dict(decrypted)
    merged = dict(payload)
    for key in ("password", "recovery", "2fa"):
        if not isinstance(merged.get(key), str) or not merged[key].strip():
            merged[key] = existing.get(key)
    if existing.get("proxy"):
        merged["proxy"] = existing["proxy"]
    if any(not isinstance(merged.get(key), str) or not merged[key].strip() for key in ("password", "recovery", "2fa")):
        raise ImportValidationError("The existing encrypted credential set is incomplete; provide all credential fields")
    encrypted_payload = SecretCipher().encrypt(merged)
    if account.secret:
        account.secret.encrypted_payload = encrypted_payload
        account.secret.key_version = "v1"
    else:
        account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", key_version="v1", encrypted_payload=encrypted_payload)
    account.recovery_hint = mask_recovery(merged["recovery"])
    account.two_fa_configured = bool(merged["2fa"])
    account.readiness_status = "READY_FOR_LOGIN"
    account.login_status = "REQUIRED"
    profile = account.browser_profile
    if profile is not None:
        # Rotating credentials invalidates the previous authenticated browser
        # state. The next login must establish a fresh saved profile.
        profile.authenticated_at = None
        profile.status = "RESERVED"
        account.profile_status = "RESERVED"
    for revision in db.scalars(select(CredentialRevision).where(CredentialRevision.account_id == account.id, CredentialRevision.status == "ACTIVE")).all():
        revision.status = "RETIRED"
    latest = int(db.scalar(select(func.max(CredentialRevision.version)).where(CredentialRevision.account_id == account.id)) or 0)
    version = latest + 1
    db.add(CredentialRevision(id=_new_id("credrev"), organization_id=account.organization_id, account_id=account.id, version=version, secret_ref=f"secret://account/{account.id}/v{version}", encrypted_payload=encrypted_payload, recovery_hint=account.recovery_hint, two_fa_configured=account.two_fa_configured, status="ACTIVE", created_by=actor_id))
    return version


def credential_records(db: Session, organization_id: str, q: str | None, group_id: str | None, status: str | None, page: int, page_size: int, visible_group_ids: set[str] | None = None) -> dict:
    filters = [GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED", GmailAccount.account_type != "EXTERNAL_RECIPIENT"]
    if visible_group_ids is not None:
        filters.append(GmailAccount.group_id.in_(visible_group_ids or {"__no_access__"}))
    if q:
        term = f"%{q.strip().lower()}%"
        filters.append(or_(GmailAccount.email.ilike(term), GmailAccount.id.ilike(term), AccountGroup.name.ilike(term)))
    if group_id:
        filters.append(GmailAccount.group_id == group_id)
    rows = db.scalars(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).join(AccountGroup, GmailAccount.group_id == AccountGroup.id).where(*filters).order_by(GmailAccount.updated_at.desc())).unique().all()
    records: list[dict] = []
    for account in rows:
        payload: dict = {}
        if account.secret:
            try:
                decrypted = SecretCipher().decrypt(account.secret.encrypted_payload)
                if isinstance(decrypted, dict):
                    payload = decrypted
            except Exception:  # noqa: BLE001 - a corrupt vault must surface as missing, never as a leak
                payload = {}
        password_present = bool(str(payload.get("password") or "").strip())
        if status in ("STORED", "STORED_ENCRYPTED") and not password_present:
            continue
        if status == "MISSING" and password_present:
            continue
        if status == "REQUIRED" and account.login_status != "REQUIRED":
            continue
        active = db.scalar(select(CredentialRevision).where(CredentialRevision.account_id == account.id, CredentialRevision.status == "ACTIVE").order_by(CredentialRevision.version.desc()).limit(1))
        latest = db.scalar(select(func.max(CredentialRevision.version)).where(CredentialRevision.account_id == account.id)) or 0
        records.append({
            "id": account.id,
            "email": account.email,
            "group_id": account.group_id,
            "group_name": account.group.name,
            "secret_status": "STORED_ENCRYPTED" if account.secret else "MISSING",
            "password_present": password_present,
            "recovery_masked": account.recovery_hint,
            "two_fa_configured": account.two_fa_configured,
            "credential_status": "STORED_ENCRYPTED" if password_present else "MISSING",
            "active_version": active.version if active else None,
            "latest_version": latest,
            "login_status": account.login_status,
            "profile_status": "SAVED" if account.browser_profile and account.browser_profile.authenticated_at and not account.browser_profile.active_session_id else account.profile_status,
            "updated_at": account.updated_at,
        })
    total = len(records)
    start = (page - 1) * page_size
    return {"items": records[start:start + page_size], "page": page, "page_size": page_size, "total": total}


def rotate_account_credentials(db: Session, organization_id: str, actor_id: str, account_id: str) -> dict:
    """Regenerate the account password server-side and return it exactly once.

    The fresh password is stored encrypted in the vault and exposed as
    plaintext only on this single response, mirroring the one-time TOTP
    provisioning used by the automated credential-change confirm step. The
    operator applies it to Google through the automated change plan (or the
    saved-session manual flow) while the current plaintext stays at rest.
    """
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot rotate credentials")
    payload: dict = {}
    if account.secret:
        try:
            decrypted = SecretCipher().decrypt(account.secret.encrypted_payload) or {}
            if isinstance(decrypted, dict):
                payload = dict(decrypted)
        except Exception as exc:  # noqa: BLE001
            raise ImportValidationError("The encrypted account vault could not be opened") from exc
    for key in ("recovery", "2fa"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ImportValidationError("The existing credential set is incomplete; complete the recovery and 2FA fields before rotating the password")
    has_proxy = isinstance(payload.get("proxy"), dict) and bool(payload.get("proxy"))
    new_password = _generate_password()
    payload["password"] = new_password
    for revision in db.scalars(select(CredentialRevision).where(CredentialRevision.account_id == account.id, CredentialRevision.status == "ACTIVE")).all():
        revision.status = "RETIRED"
    if account.secret:
        account.secret.encrypted_payload = SecretCipher().encrypt(payload)
        account.secret.key_version = "v1"
    else:
        account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", key_version="v1", encrypted_payload=SecretCipher().encrypt(payload))
    account.recovery_hint = mask_recovery(payload["recovery"])
    account.two_fa_configured = bool(payload["2fa"])
    account.readiness_status = "READY_FOR_LOGIN"
    account.login_status = "REQUIRED"
    if account.browser_profile:
        account.browser_profile.authenticated_at = None
        account.browser_profile.status = "RESERVED"
        account.profile_status = "RESERVED"
    version = int(db.scalar(select(func.max(CredentialRevision.version)).where(CredentialRevision.account_id == account.id)) or 0) + 1
    db.add(CredentialRevision(id=_new_id("credrev"), organization_id=organization_id, account_id=account.id, version=version, secret_ref=f"secret://account/{account.id}/v{version}", encrypted_payload=SecretCipher().encrypt(payload), recovery_hint=mask_recovery(payload["recovery"]), two_fa_configured=bool(payload["2fa"]), status="ACTIVE", created_by=actor_id))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.credentials_regenerated", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"email": account.email, "version": version, "revealed_once": True, "proxy_preserved": has_proxy}))
    db.commit()
    return {"account_id": account.id, "email": account.email, "password": new_password, "recovery_masked": account.recovery_hint, "two_fa_configured": account.two_fa_configured, "version": version}


def credential_plaintext(db: Session, organization_id: str, actor_id: str, account_id: str) -> dict:
    """Return the currently saved password so the operator can copy the real
    value (e.g. after an automated credential change confirmed it). The caller
    is audited; the password is surfaced as plaintext only to this response.
    """
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.group), joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts do not expose credentials")
    payload: dict = {}
    if account.secret:
        try:
            decrypted = SecretCipher().decrypt(account.secret.encrypted_payload) or {}
            if isinstance(decrypted, dict):
                payload = dict(decrypted)
        except Exception as exc:  # noqa: BLE001
            raise ImportValidationError("The encrypted account vault could not be opened") from exc
    password = str(payload.get("password") or "").strip()
    if not password:
        raise ImportValidationError("No stored password is available for this account; rotate or change credentials first")
    active = db.scalar(select(CredentialRevision).where(CredentialRevision.account_id == account.id, CredentialRevision.status == "ACTIVE").order_by(CredentialRevision.version.desc()).limit(1))
    latest = int(db.scalar(select(func.max(CredentialRevision.version)).where(CredentialRevision.account_id == account.id)) or 0)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="account.credentials_copied", target_type="gmail_account", target_id=account.id, result="success", metadata_json={"email": account.email, "version": latest}))
    db.commit()
    return {"account_id": account.id, "email": account.email, "password": password, "recovery": str(payload.get("recovery") or "").strip(), "recovery_masked": str(account.recovery_hint or ""), "two_fa_configured": bool(payload.get("2fa") and str(payload.get("2fa") or "").strip()), "active_version": active.version if active else None, "latest_version": latest}


def _credential_change_plan_response(plan: CredentialChangePlan, provisioned_two_fa_secret: str | None = None) -> dict:
    return {
        "id": plan.id,
        "account_id": plan.account_id,
        "account_email": plan.account.email,
        "version": plan.version,
        "status": plan.status,
        "password_requested": plan.password_requested,
        "recovery_requested": plan.recovery_requested,
        "two_fa_requested": plan.two_fa_requested,
        "recovery_hint": plan.recovery_hint,
        "recovery_count": plan.recovery_count,
        "session_id": plan.session_id,
        "provisioned_two_fa_secret": provisioned_two_fa_secret,
        "created_by": plan.created_by,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "started_at": plan.started_at,
        "confirmed_at": plan.confirmed_at,
        "completed_at": plan.completed_at,
        "failure_reason": plan.failure_reason,
    }


def _credential_plan_is_live(db: Session, plan: CredentialChangePlan) -> bool:
    """True when the plan's saved session is being driven by a live browser right now."""
    if not plan.session_id:
        return False
    session = db.get(BrowserSession, plan.session_id)
    if session is None or session.state not in ACTIVE_SESSION_STATES:
        return False
    browser_opened = bool((session.metadata_json or {}).get("browser_opened"))
    return browser_opened and local_browser_is_alive(session.profile_id, session.id)


def create_credential_change_plan(db: Session, organization_id: str, actor_id: str, account_id: str, password_requested: bool, recovery_requested: bool, two_fa_requested: bool, recovery_emails: list[str]) -> dict:
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot receive credential change plans")
    active = db.scalar(select(CredentialChangePlan).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.account_id == account_id, CredentialChangePlan.status.in_(CREDENTIAL_CHANGE_ACTIVE_STATES)))
    if active:
        try:
            previous_pool = set((SecretCipher().decrypt(active.recovery_pool_encrypted) or {}).get("emails", [])) if active.recovery_pool_encrypted else set()
        except Exception:  # noqa: BLE001 - the encrypted pool must never break a retry verdict
            previous_pool = set()
        same_fields = {active.password_requested, active.recovery_requested, active.two_fa_requested} == {password_requested, recovery_requested, two_fa_requested}
        if same_fields and set(recovery_emails) == previous_pool:
            return _credential_change_plan_response(db.scalar(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == active.id)))
        # A changed selection must not stay stuck behind a stale plan for the
        # same account: retire the previous plan and prepare the newly chosen
        # fields. Refuse only when the old plan's Google session is genuinely
        # being driven by a live browser this very moment.
        if _credential_plan_is_live(db, active):
            raise ImportValidationError(f"This account is already changing credentials in a live Google session (plan v{active.version}). Finish or cancel it first, then pick another selection.")
        cancel_credential_change_plan(db, organization_id, actor_id, active.id)
    normalized_pool: list[str] = []
    for raw in recovery_emails:
        email = raw.strip().lower()
        if not EMAIL_RE.match(email):
            raise ImportValidationError("Enter a valid recovery email address")
        if email == account.normalized_email:
            raise ImportValidationError("A recovery address cannot be the account's own email")
        if email not in normalized_pool:
            normalized_pool.append(email)
    if recovery_requested and not normalized_pool:
        raise ImportValidationError("Recovery rotation requires at least one real recovery email address")
    fields = [name for name, on in (("password", password_requested), ("recovery", recovery_requested), ("2fa", two_fa_requested)) if on]
    version = (db.scalar(select(func.max(CredentialChangePlan.version)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.account_id == account_id)) or 0) + 1
    assigned = normalized_pool[0] if normalized_pool else ""
    plan = CredentialChangePlan(id=_new_id("ccplan"), organization_id=organization_id, account_id=account_id, version=version, status="DRAFT", password_requested=password_requested, recovery_requested=recovery_requested, two_fa_requested=two_fa_requested, recovery_pool_encrypted=SecretCipher().encrypt({"emails": normalized_pool}) if normalized_pool else None, recovery_hint=mask_recovery(assigned) if assigned else None, recovery_count=len(normalized_pool), configuration_json={"fields": fields, "rotation_mode": "one_recovery_per_account"})
    db.add(plan)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="credential_change_plan.created", target_type="credential_change_plan", target_id=plan.id, result="success", metadata_json={"account_id": account_id, "version": version, "fields": fields, "recovery_pool_count": len(normalized_pool), "recovery_rotating": recovery_requested}))
    db.commit()
    return _credential_change_plan_response(db.scalar(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan.id)))


def list_credential_change_plans(db: Session, organization_id: str, account_id: str) -> list[dict]:
    if not db.scalar(select(GmailAccount.id).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id)):
        raise ImportValidationError("Account not found")
    rows = db.scalars(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.account_id == account_id).order_by(CredentialChangePlan.created_at.desc())).all()
    return [_credential_change_plan_response(row) for row in rows]


def start_credential_change_plan(db: Session, organization_id: str, actor_id: str, plan_id: str) -> dict:
    plan = db.scalar(select(CredentialChangePlan).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan_id))
    if not plan:
        raise ImportValidationError("Credential change plan not found")
    if plan.status in CREDENTIAL_CHANGE_TERMINAL_STATES:
        raise ImportValidationError(f"A {plan.status.lower().replace('_', ' ')} credential plan cannot be started")
    if plan.status not in {"DRAFT", "READY_FOR_OPERATOR"}:
        raise ImportValidationError(f"A {plan.status.lower().replace('_', ' ')} credential plan cannot be started")
    if plan.session_id:
        linked = get_session(db, organization_id, plan.session_id)
        if linked:
            if linked.state in ACTIVE_SESSION_STATES:
                browser_opened = bool((linked.metadata_json or {}).get("browser_opened"))
                browser_alive = browser_opened and local_browser_is_alive(linked.profile_id, linked.id)
                if browser_alive or not get_settings().browser_launch_enabled:
                    raise ImportValidationError("This credential plan is already running its saved Google session")
            try:
                close_session(db, organization_id, actor_id, linked.id)
            except Exception:  # noqa: BLE001 - closing a stale session must never block a restart
                db.rollback()
            linked_metadata = dict(linked.metadata_json or {})
            linked_metadata.pop("credential_change_pending", None)
            linked.metadata_json = linked_metadata
            db.commit()
        plan.session_id = None
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == plan.account_id).with_for_update())
    if account is None:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot execute credential change plans")
    profile = account.browser_profile
    if profile is None or profile.authenticated_at is None:
        raise ImportValidationError("No authenticated saved profile for this account — complete a login session first so the Google change can be made in the saved session")
    session = start_login_session(db, organization_id, actor_id, plan.account_id, purpose="credential_change")
    plan = db.scalar(select(CredentialChangePlan).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan_id).with_for_update())
    plan.session_id = session.id
    plan.status = "READY_FOR_OPERATOR"
    plan.started_at = utcnow()
    configuration = dict(plan.configuration_json or {})
    configuration.update({"automated": True, "google_change_page": _credential_change_start_url(session.id, organization_id), "google_change_session_id": session.id})
    plan.configuration_json = configuration
    # Roll the new password / authenticator secret server-side now. The observer
    # types the password into Google's password-change form and the secret is
    # written to the profile sheets; the confirm step then stores exactly the
    # values the automation used, encrypted inside the account vault.
    pending: dict[str, object] = {}
    if plan.password_requested:
        pending["password"] = _generate_password()
    if plan.two_fa_requested:
        pending["2fa"] = _new_totp_secret()
    if plan.recovery_requested:
        # Rotate to the plan's first pool address; the observer types it into
        # Google's recovery-email page and the auto-persist stores it on success.
        recovery_pool: list[str] = []
        if plan.recovery_pool_encrypted:
            try:
                recovery_pool = (SecretCipher().decrypt(plan.recovery_pool_encrypted) or {}).get("emails") or []
            except Exception:  # noqa: BLE001 - a broken pool blocks the leg, not the session
                recovery_pool = []
        if recovery_pool:
            pending["recovery"] = str(recovery_pool[0])
    if pending:
        pending["password_requested"] = plan.password_requested
        pending["two_fa_requested"] = plan.two_fa_requested
        pending["recovery_requested"] = plan.recovery_requested
        pending["encrypted_at"] = utcnow().isoformat()
        session_metadata = dict(session.metadata_json or {})
        session_metadata["credential_change_pending"] = SecretCipher().encrypt(pending)
        session.metadata_json = session_metadata
        # The observer thread commits session metadata writes concurrently and can
        # overwrite a just-failed commit (lost update) before "confirm" reads it,
        # which made automated changes appear to produce no password. Persist the
        # material on the plan as the durable source of truth as well.
        configuration["material_encrypted"] = SecretCipher().encrypt(dict(pending))
        plan.configuration_json = configuration
    elif configuration.get("material_encrypted"):
        # Re-running this plan without password/2FA requested must never reuse
        # material generated (and possibly typed into Google) by an earlier run.
        configuration.pop("material_encrypted", None)
        plan.configuration_json = configuration
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="credential_change_plan.started", target_type="credential_change_plan", target_id=plan.id, result="success", metadata_json={"account_id": plan.account_id, "version": plan.version, "session_id": session.id, "fields": (plan.configuration_json or {}).get("fields")}))
    db.commit()
    return _credential_change_plan_response(db.scalar(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan.id)))


def confirm_credential_change_plan(db: Session, organization_id: str, actor_id: str, plan_id: str, acknowledged: bool, password: str | None, two_fa: str | None, recovery: str | None) -> dict:
    plan = db.scalar(select(CredentialChangePlan).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan_id).with_for_update())
    if not plan:
        raise ImportValidationError("Credential change plan not found")
    if plan.status == "STORED_ENCRYPTED":
        # The observer may already have auto-stored the material the moment the
        # Google change completed; a repeat confirm is idempotent.
        return _credential_change_plan_response(db.scalar(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan.id)))
    if plan.status in CREDENTIAL_CHANGE_TERMINAL_STATES:
        raise ImportValidationError(f"A {plan.status.lower().replace('_', ' ')} credential plan cannot be confirmed")
    if not acknowledged:
        raise ImportValidationError("Confirm that the credential change was completed inside the Google session")
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.id == plan.account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot execute credential change plans")
    pending: dict = {}
    if plan.session_id:
        pending_session = db.get(BrowserSession, plan.session_id)
        if pending_session is not None:
            observer_attached = bool((pending_session.metadata_json or {}).get("observer_attached"))
            if observer_attached:
                login_state = str(pending_session.login_state or "")
                if login_state == "CREDENTIAL_CHANGE_FAILED":
                    raise ImportValidationError("The automated credential change failed inside Google; cancel this plan and retry it")
                if login_state not in {"CREDENTIAL_CHANGE_COMPLETED", ""}:
                    raise ImportValidationError("Wait for the automated credential change to finish in the Google session, then confirm")
        # The plan is the durable source of truth for the automated material
        # (the observer thread can clobber the session metadata blob); the session
        # blob remains the fallback for plans started before that was introduced.
        encrypted = (plan.configuration_json or {}).get("material_encrypted")
        if not encrypted and pending_session is not None:
            encrypted = (pending_session.metadata_json or {}).get("credential_change_pending")
        if encrypted:
            try:
                pending = SecretCipher().decrypt(encrypted) or {}
            except Exception as exc:  # noqa: BLE001
                raise ImportValidationError("The automated credential material could not be opened") from exc
            if not isinstance(pending, dict):
                pending = {}
    new_password = str((pending or {}).get("password") or "").strip() or (password or "").strip()
    if plan.password_requested and not new_password:
        raise ImportValidationError("The automated password change did not produce a new password; re-run the plan from the credential change panel")
    new_two_fa = str((pending or {}).get("2fa") or "").strip() or (two_fa or "").strip()
    provisioned = None
    if plan.two_fa_requested:
        if not new_two_fa:
            # Server-side secret generation. The secret is shown once on this
            # confirm response so the operator can configure their authenticator,
            # then only stored encrypted inside the account vault.
            new_two_fa = _new_totp_secret()
        provisioned = new_two_fa
    new_recovery = None
    if plan.recovery_requested:
        # Prefer the address the automation actually typed into Google so the
        # vault cannot drift from the live account; fall back to the operator's
        # entry, then to the rotation pool head.
        new_recovery = str((pending or {}).get("recovery") or "").strip() or (recovery or "").strip()
        pool: list[str] = []
        if plan.recovery_pool_encrypted:
            try:
                pool = (SecretCipher().decrypt(plan.recovery_pool_encrypted) or {}).get("emails") or []
            except Exception as exc:  # noqa: BLE001
                raise ImportValidationError("The encrypted recovery rotation pool could not be opened") from exc
        if not new_recovery:
            new_recovery = pool[0] if pool else ""
        normalized_recovery = new_recovery.strip().lower()
        if not EMAIL_RE.match(normalized_recovery):
            raise ImportValidationError("Enter a valid recovery email address")
        if normalized_recovery not in {item.strip().lower() for item in pool}:
            raise ImportValidationError("The recovery address must be one of the addresses in this plan's rotation pool")
    payload: dict = {"password": new_password if plan.password_requested else None, "recovery": new_recovery if plan.recovery_requested else None, "2fa": new_two_fa if plan.two_fa_requested else None}
    version = _rotate_account_credentials_in_db(db, account, payload, actor_id)
    now = utcnow()
    plan.status = "STORED_ENCRYPTED"
    plan.confirmed_at = now
    plan.completed_at = now
    configuration = dict(plan.configuration_json or {})
    configuration.update({"google_change_confirmed": True, "revision_version": version})
    configuration.pop("material_encrypted", None)
    plan.configuration_json = configuration
    if plan.session_id:
        pending_session = db.get(BrowserSession, plan.session_id)
        if pending_session is not None:
            pending_metadata = dict(pending_session.metadata_json or {})
            pending_metadata.pop("credential_change_pending", None)
            if pending_session.state in ACTIVE_SESSION_STATES:
                # A manual confirm ends the run: stop the observer and close the
                # session so it cannot keep re-driving the Google change page
                # after the operator has already confirmed the result. The
                # account's fresh-login requirement set by the rotation is kept.
                close_local_browser(pending_session.id)
                _stop_observer(pending_session.id)
                pending_session.state = "CLOSED"
                pending_session.login_state = "CREDENTIAL_CHANGE_COMPLETED"
                pending_session.closed_at = now
                pending_session.last_event_at = now
                pending_metadata["browser_opened"] = False
            pending_session.metadata_json = pending_metadata
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="credential_change_plan.confirmed", target_type="credential_change_plan", target_id=plan.id, result="success", metadata_json={"account_id": account.id, "version": plan.version, "revision_version": version, "secret_status": "encrypted", "reauth_required": True, "fields": (plan.configuration_json or {}).get("fields"), "password_rotated": plan.password_requested, "recovery_rotated": plan.recovery_requested, "two_fa_rotated": plan.two_fa_requested, "two_fa_provisioned_server_side": bool(provisioned)}))
    db.commit()
    return _credential_change_plan_response(db.scalar(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan.id)), provisioned)


def cancel_credential_change_plan(db: Session, organization_id: str, actor_id: str, plan_id: str) -> dict:
    plan = db.scalar(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan_id).with_for_update())
    if not plan:
        raise ImportValidationError("Credential change plan not found")
    if plan.status in CREDENTIAL_CHANGE_TERMINAL_STATES:
        raise ImportValidationError(f"A {plan.status.lower().replace('_', ' ')} credential plan cannot be cancelled")
    if plan.session_id:
        try:
            close_session(db, organization_id, actor_id, plan.session_id)
        except ImportValidationError:
            pass
    plan.status = "CANCELLED"
    plan.completed_at = utcnow()
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="credential_change_plan.cancelled", target_type="credential_change_plan", target_id=plan.id, result="success", metadata_json={"account_id": plan.account_id, "version": plan.version, "fields": (plan.configuration_json or {}).get("fields")}))
    db.commit()
    return _credential_change_plan_response(db.scalar(select(CredentialChangePlan).options(joinedload(CredentialChangePlan.account)).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.id == plan.id)))


def _login_job_response(job: LoginJob, items: list[LoginJobItem] | None = None) -> dict:
    rows = items if items is not None else list(job.items)
    return {"id": job.id, "group_id": job.group_id, "operation_type": job.operation_type or "login", "concurrency_limit": job.concurrency_limit, "status": job.status, "total_count": job.total_count, "queued_count": job.queued_count, "running_count": job.running_count, "succeeded_count": job.succeeded_count, "attention_count": job.attention_count, "failed_count": job.failed_count, "created_at": job.created_at, "started_at": job.started_at, "completed_at": job.completed_at, "updated_at": job.updated_at, "items": [{"id": item.id, "account_id": item.account_id, "account_email": item.account.email, "session_id": item.session_id, "state": item.state, "attempts": item.attempts, "last_error": item.last_error, "paused_reason": item.paused_reason, "updated_at": item.updated_at} for item in rows]}


def create_login_job(db: Session, organization_id: str, actor_id: str, group_id: str | None, account_ids: list[str], concurrency_limit: int, operation_type: str = "login", operation_config: dict[str, object] | None = None) -> dict:
    if group_id and not db.scalar(select(AccountGroup.id).where(AccountGroup.organization_id == organization_id, AccountGroup.id == group_id)):
        raise ImportValidationError("Choose a valid destination group")
    query = select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED")
    # A login batch is an explicit operator instruction to open the selected
    # account profiles. Do not silently omit selected accounts just because
    # registry readiness is not READY_FOR_LOGIN; the session can still be
    # useful for a manual sign-in or verification handoff.
    if group_id:
        query = query.where(GmailAccount.group_id == group_id)
    if account_ids:
        query = query.where(GmailAccount.id.in_(list(dict.fromkeys(account_ids))))
    accounts = db.scalars(query.order_by(GmailAccount.created_at)).all()
    if not accounts:
        raise ImportValidationError("No accounts are ready for a login batch")
    if len(accounts) > 300:
        raise ImportValidationError("A login batch is limited to 300 accounts")
    job = LoginJob(id=_new_id("loginjob"), organization_id=organization_id, group_id=group_id, operation_type=operation_type, operation_config=dict(operation_config or {}), concurrency_limit=concurrency_limit, status="QUEUED", total_count=len(accounts), queued_count=len(accounts), created_by=actor_id)
    db.add(job)
    for account in accounts:
        db.add(LoginJobItem(id=_new_id("loginitem"), job_id=job.id, organization_id=organization_id, account_id=account.id, state="QUEUED"))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action=f"{operation_type}_job.created", target_type="login_job", target_id=job.id, result="success", metadata_json={"group_id": group_id, "account_count": len(accounts), "concurrency_limit": concurrency_limit, "max_messages": operation_config.get("max_messages") if operation_config else None, "mode": "isolated_profile_queue"}))
    db.commit()
    job = db.scalar(select(LoginJob).options(joinedload(LoginJob.items).joinedload(LoginJobItem.account)).where(LoginJob.id == job.id))
    enqueue_queued_items(db, job.id)
    return _login_job_response(job)


def create_mail_workflow_job(db: Session, organization_id: str, actor_id: str, group_id: str | None, account_ids: list[str], concurrency_limit: int, workflow: dict[str, object]) -> dict:
    """Queue one bounded Gmail workflow per account under the shared lease."""
    if workflow.get("action") == "confirmation_review" and workflow.get("review_mode") != "queue":
        raise ImportValidationError("Use confirmation queue mode for a bulk confirmation review")
    if workflow.get("action") == "confirmation_review" and not 1 <= concurrency_limit <= 10:
        raise ImportValidationError("Confirmation queues support 1 to 10 concurrent account sessions")
    return create_login_job(db, organization_id, actor_id, group_id, account_ids, concurrency_limit, operation_type="mail", operation_config=workflow)


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


def _credential_change_start_url(session_id: str, organization_id: str | None) -> str:
    """Pick the Google page a credential-change observer should open.

    A 2FA-only plan must open the Two-Step authenticator page directly; every
    other credential change (password, or password + 2FA) starts on the
    password page. This avoids the observer landing a 2FA-only session on the
    password-change surface -- which both looks wrong and, with password table
    material, could change the password instead of the authenticator.
    """
    two_step_url = "https://myaccount.google.com/two-step-verification/authenticator"
    recovery_url = "https://myaccount.google.com/recovery/email"
    password_url = "https://myaccount.google.com/signinoptions/password"
    try:
        db = SessionLocal()
        try:
            session = db.get(BrowserSession, session_id)
            if not session or (organization_id and session.organization_id != organization_id):
                return password_url
            # The observer launches inside start_credential_change_plan BEFORE
            # that function links the plan to the session, so a plan matched by
            # session_id alone is not visible yet. Only one active plan can
            # exist per account, so fall back to the account's newest active
            # plan when the session is not linked.
            linked = db.scalar(
                select(CredentialChangePlan).where(
                    CredentialChangePlan.organization_id == session.organization_id,
                    CredentialChangePlan.session_id == session_id,
                    CredentialChangePlan.status.in_(CREDENTIAL_CHANGE_ACTIVE_STATES),
                ).order_by(CredentialChangePlan.started_at.desc().nullslast()).limit(1)
            )
            plan = linked
            if plan is None:
                plan = db.scalar(
                    select(CredentialChangePlan).where(
                        CredentialChangePlan.organization_id == session.organization_id,
                        CredentialChangePlan.account_id == session.account_id,
                        or_(CredentialChangePlan.session_id.is_(None), CredentialChangePlan.session_id == session_id),
                        CredentialChangePlan.status.in_(CREDENTIAL_CHANGE_ACTIVE_STATES),
                    ).order_by(CredentialChangePlan.created_at.desc()).limit(1)
                )
            if plan is not None:
                # Land the session on the page for the first requested leg:
                # password → recovery → 2-Step, in that order.
                if not plan.password_requested and plan.recovery_requested:
                    return recovery_url
                if not plan.password_requested and plan.two_fa_requested:
                    return two_step_url
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - default to the password page on any failure
        logger.exception("could not decide the credential-change start page for session %s", session_id)
    return password_url


def _resolve_two_captcha_key(organization_id: str) -> str | None:
    with SessionLocal() as db:
        row = db.scalar(select(WorkspaceSettings).where(WorkspaceSettings.organization_id == organization_id))
        if not row or not row.two_captcha_enabled or not row.two_captcha_api_key_encrypted:
            return None
        try:
            return str(SecretCipher().decrypt(row.two_captcha_api_key_encrypted).get("value") or "") or None
        except Exception:  # noqa: BLE001 - do not expose a damaged secret
            logger.warning("Unable to decrypt the workspace 2Captcha key")
            return None


def _start_observer(session_id: str, profile_id: str, organization_id: str, actor_id: str, purpose: str = "login", contacts_csv_path: str | None = None, addon_candidates: list[dict[str, str]] | None = None, mail_workflow: dict[str, object] | None = None, alerts_topics: list[str] | None = None, newsletter_config: dict[str, object] | None = None, account_preferences: dict[str, object] | None = None) -> bool:
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
        headless=bool((newsletter_config or {}).get("headless_browser")) if purpose == "newsletter" else False,
        start_url=(
            "https://contacts.google.com/"
            if purpose == "contacts"
            else "https://mail.google.com/mail/u/0/?tab=rm&ogbl#settings/inbox"
            if purpose == "labels_markers"
            else "https://myaccount.google.com/account-enhanced-safe-browsing"
            if purpose == "security"
            else _credential_change_start_url(session_id, organization_id)
            if purpose == "credential_change"
            else "https://mail.google.com/"
            if purpose == "reuse"
            else "https://mail.google.com/mail/u/0/#inbox"
            if purpose == "addons"
            else "https://mail.google.com/mail/u/0/#spam"
            if purpose == "mail" and (mail_workflow or {}).get("action") == "spam_to_inbox"
            else "https://mail.google.com/mail/u/0/#inbox"
            if purpose == "mail" and (mail_workflow or {}).get("action") in {"reply", "confirmation_review"}
            else "https://mail.google.com/mail/u/0/#category/promotions"
            if purpose == "mail"
            else "https://www.google.com/alerts"
            if purpose == "alerts"
            else "https://myaccount.google.com/personal-info"
            if purpose == "account_preferences"
            else "https://mail.google.com/mail/u/0/#inbox"
            if purpose == "newsletter_confirm"
            else str((newsletter_config or {}).get("signup_url") or "")
            if purpose == "newsletter"
            else None
        ),
        observe_session_page=lambda sid, url, title, text: _observe_in_own_session(organization_id, actor_id, sid, url, title, text),
        resolve_credentials=lambda sid: _resolve_login_credentials(sid),
        resolve_otp_secret=lambda sid: _resolve_login_otp_secret(sid),
        captcha_api_key=_resolve_two_captcha_key(organization_id),
        resolve_proxy=lambda sid: _resolve_proxy_options(sid),
        resolve_current_password=lambda sid: _resolve_current_password(sid),
        resolve_credential_change=lambda sid: _resolve_credential_change_material(sid, organization_id),
        store_credential_change=lambda sid, material: _store_credential_change_material(sid, organization_id, material),
        report_event=_report_observer_event,
        on_browser_closed=_on_observer_browser_closed,
        interval=settings.browser_observe_interval,
        settle_seconds=settings.browser_observe_settle_seconds,
        purpose=purpose,
        contacts_csv_path=contacts_csv_path,
        addon_candidates=addon_candidates,
        mail_workflow=mail_workflow,
        alerts_topics=alerts_topics,
        newsletter_config=newsletter_config,
        account_preferences=account_preferences,
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


def _maybe_start_newsletter_confirmation(db: Session, session: BrowserSession) -> None:
    """Chain a standalone verified signup into Gmail confirmation.

    Signup and confirmation deliberately use separate observer sessions so the
    same account profile is never opened by two browser contexts at once. The
    signup observer reports its terminal result first; this helper runs only
    after the browser-close callback has released the profile reservation.
    Bulk jobs keep their existing durable-task dispatcher path and therefore
    are excluded here to avoid starting two confirmation sessions.
    """
    metadata = dict(session.metadata_json or {})
    if (
        metadata.get("purpose") != "newsletter"
        or metadata.get("newsletter_external_recipient")
        or session.state != "CLOSED"
        or session.login_state != "NEWSLETTER_SUBMITTED"
        or not metadata.get("newsletter_auto_confirm")
        or not metadata.get("newsletter_confirmation_required")
        or metadata.get("newsletter_job_id")
        or metadata.get("newsletter_confirmation_session_id")
    ):
        return

    newsletter_id = str(metadata.get("newsletter_id") or "")
    profile_id = str(metadata.get("newsletter_profile_id") or "")
    subscription_id = str(metadata.get("newsletter_subscription_id") or "")
    if not newsletter_id or not subscription_id:
        return

    subscription = db.get(NewsletterSubscription, subscription_id)
    if subscription is None or subscription.state not in {"SUBMITTED", "WAITING_CONFIRMATION"}:
        return

    try:
        confirmation_session = start_newsletter_confirmation_test(
            db,
            session.organization_id,
            "system",
            newsletter_id,
            session.account_id,
            profile_id,
            subscription_id,
        )
        metadata["newsletter_confirmation_session_id"] = confirmation_session.id
        metadata["newsletter_result"] = "WAITING_CONFIRMATION"
        session.metadata_json = metadata
        db.commit()
        logger.info("newsletter signup %s chained to Gmail confirmation %s", session.id, confirmation_session.id)
    except Exception as exc:  # noqa: BLE001 - persist attention instead of retrying blindly
        db.rollback()
        current = db.get(BrowserSession, session.id)
        subscription = db.get(NewsletterSubscription, subscription_id)
        detail = "Automatic Gmail confirmation could not be started"
        if subscription is not None:
            subscription.state = "MANUAL_REVIEW"
            subscription.last_error = detail
            subscription.last_checked_at = utcnow()
        if current is not None:
            current_metadata = dict(current.metadata_json or {})
            current_metadata["newsletter_result"] = "MANUAL_REVIEW"
            current_metadata["newsletter_confirmation_error"] = type(exc).__name__
            current.metadata_json = current_metadata
        db.add(
            AuditEvent(
                id=_new_id("audit"),
                organization_id=session.organization_id,
                actor_id="system",
                action="newsletter.confirmation.manual_review",
                target_type="newsletter_subscription",
                target_id=subscription_id,
                result="attention",
                metadata_json={"newsletter_id": newsletter_id, "account_id": session.account_id, "reason": "confirmation_start_failed"},
            )
        )
        db.commit()
        logger.warning("newsletter signup %s could not start Gmail confirmation: %s", session.id, type(exc).__name__)


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
        newsletter_job = None
        # A newsletter observer can disappear without emitting its terminal
        # event (browser crash, operator close, or a Playwright context error).
        # Do not let that leave a PROCESSING task attached to a dead session:
        # stale recovery would otherwise requeue the same recipient and open
        # the same site again indefinitely.  The outcome is deliberately
        # MANUAL_REVIEW because the signup may have reached the provider even
        # though no success evidence was recorded.
        newsletter_task_id = metadata.get("newsletter_task_id")
        NewsletterJobModel, NewsletterTaskModel = _newsletter_models()
        newsletter_task = db.get(NewsletterTaskModel, str(newsletter_task_id)) if newsletter_task_id else None
        if newsletter_task is not None:
            # Terminal signup events can be committed before Playwright has
            # finished closing the persistent browser context. Keep the job
            # reference so this post-close callback can dispatch the next task
            # even if the earlier event-triggered dispatch raced the shutdown.
            newsletter_job = db.get(NewsletterJobModel, newsletter_task.job_id)
        if session.state in ACTIVE_SESSION_STATES and newsletter_task_id:
            from .newsletter_service import _newsletter_job_complete, _record_task_event, _release_newsletter_task_lease

            task = newsletter_task
            if task is not None and task.state == "PROCESSING":
                task.state = "MANUAL_REVIEW"
                task.last_error = "Browser closed before a terminal newsletter result was observed"
                _release_newsletter_task_lease(db, task, "browser_closed_before_terminal_result")
                task.browser_session_id = None
                _record_task_event(db, task, "browser_closed_before_terminal_result", "attention", task.last_error)
                job = db.get(NewsletterJobModel, task.job_id)
                if job is not None:
                    _newsletter_job_complete(db, job)
                    newsletter_job = job
                now = utcnow()
                session.state = "CLOSED"
                session.closed_at = now
                session.last_event_at = now
                session.login_state = "NEWSLETTER_MANUAL_REVIEW"
                session.failure_reason = task.last_error
                session.profile.status = "RESERVED"
                session.profile.lock_state = "UNLOCKED"
                session.profile.active_session_id = None
                session.profile.last_closed_at = now
                metadata["newsletter_result"] = "MANUAL_REVIEW"
                metadata["terminal_result"] = "MANUAL_REVIEW"
                session.metadata_json = metadata
                db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="newsletter.signup.browser_closed", target_type="browser_session", target_id=session.id, result="attention", metadata_json={"account_id": session.account_id, "reason": "browser_closed_before_terminal_result"}))
        if session.state == "CLOSED" and session.login_state != "AUTHENTICATED":
            _prune_unsaved_profile_folder(session)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="session.browser_closed", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": session.account_id, "profile_id": session.profile_id}))
        db.commit()
        if session.state == "CLOSED":
            _maybe_start_newsletter_confirmation(db, session)
            if newsletter_task_id and newsletter_job is not None:
                _maybe_dispatch_newsletter_next(db, newsletter_job)
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


def _resolve_credential_change_material(session_id: str, organization_id: str | None = None) -> dict:
    """Decrypt the pending credential-change material provisioned for a session.

    The owning credential-change plan is the durable source of truth (the
    observer thread's own session-metadata writes can overwrite the session blob),
    with the session blob as the fallback for plans started before that existed.
    The observer resolves lazily once it reaches the Google password page so the
    automated fill is decrypted only at the last moment and only for the owning
    organization.
    """
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session:
            return {}
        if organization_id and session.organization_id != organization_id:
            return {}
        encrypted = None
        if organization_id:
            plan = db.scalar(select(CredentialChangePlan).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.session_id == session_id, CredentialChangePlan.status == "READY_FOR_OPERATOR").order_by(CredentialChangePlan.started_at.desc().nullslast()).limit(1))
            if plan and (plan.configuration_json or {}).get("material_encrypted"):
                encrypted = plan.configuration_json["material_encrypted"]
        if encrypted is None and session.metadata_json:
            encrypted = (session.metadata_json or {}).get("credential_change_pending")
        if not encrypted:
            return {}
        material = SecretCipher().decrypt(encrypted) or {}
        return material if isinstance(material, dict) else {}
    except Exception:  # noqa: BLE001 - a stale encrypted blob must not break the observer
        logger.exception("could not resolve credential-change material for session %s", session_id)
        return {}
    finally:
        db.close()


def _store_credential_change_material(session_id: str, organization_id: str, material: dict) -> None:
    """Persist updated credential-change material (e.g. the 2FA pairing key
    Google generated on its setup screen) onto the plan and the session so the
    confirm step and the profile sheets carry the real values."""
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session or session.organization_id != organization_id:
            return
        encrypted = SecretCipher().encrypt(dict(material))
        metadata = dict(session.metadata_json or {})
        metadata["credential_change_pending"] = encrypted
        session.metadata_json = metadata
        plan = db.scalar(select(CredentialChangePlan).where(CredentialChangePlan.organization_id == organization_id, CredentialChangePlan.session_id == session_id, CredentialChangePlan.status == "READY_FOR_OPERATOR").order_by(CredentialChangePlan.started_at.desc().nullslast()).limit(1))
        if plan:
            configuration = dict(plan.configuration_json or {})
            configuration["material_encrypted"] = encrypted
            plan.configuration_json = configuration
        db.commit()
    except Exception:  # noqa: BLE001 - observer best effort
        logger.exception("could not persist credential-change material for session %s", session_id)
        db.rollback()
    finally:
        db.close()


def _persist_password_leg(db: Session, session: BrowserSession) -> None:
    """Persist the Google-confirmed new password the moment the password leg
    completes, regardless of whether the 2FA or recovery legs have finished.

    This makes each credential-change leg independently durable: the user keeps
    their new password even if the authenticator setup stalls or is rejected.
    The final ``credential_change.completed`` path (``_auto_persist_credential_change``)
    re-writes the same password alongside the completed 2FA/recovery legs;
    ``_rotate_account_credentials_in_db`` backfills missing fields from the
    vault, so the 2FA secret is not clobbered by a None during the
    intermediate password-only persist.

    Runs inside the observer-event transaction (like ``_auto_persist_credential_change``);
    the caller commits."""
    if not session.account_id:
        return
    plan = db.scalar(
        select(CredentialChangePlan).where(
            CredentialChangePlan.organization_id == session.organization_id,
            CredentialChangePlan.session_id == session.id,
            CredentialChangePlan.status.in_(CREDENTIAL_CHANGE_ACTIVE_STATES),
        ).order_by(CredentialChangePlan.started_at.desc().nullslast()).limit(1)
    )
    if plan is None or not plan.password_requested:
        return
    if (plan.configuration_json or {}).get("password_persisted_version") is not None:
        return
    encrypted = (plan.configuration_json or {}).get("material_encrypted")
    if not encrypted and session.metadata_json:
        encrypted = (session.metadata_json or {}).get("credential_change_pending")
    if not encrypted:
        return
    try:
        pending = SecretCipher().decrypt(encrypted) or {}
    except Exception:  # noqa: BLE001
        logger.exception("could not decrypt material for password-leg persist on session %s", session.id)
        return
    if not isinstance(pending, dict):
        return
    password = str(pending.get("password") or "").strip()
    if not password:
        return
    account = db.scalar(
        select(GmailAccount).options(
            joinedload(GmailAccount.secret),
            joinedload(GmailAccount.browser_profile),
        ).where(GmailAccount.organization_id == session.organization_id, GmailAccount.id == session.account_id)
    )
    if account is None:
        return
    payload = {"password": password, "recovery": None, "2fa": None}
    version = _rotate_account_credentials_in_db(db, account, payload, "system")
    configuration = dict(plan.configuration_json or {})
    configuration["password_persisted_version"] = version
    plan.configuration_json = configuration
    db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="credential_change_plan.password_persisted", target_type="credential_change_plan", target_id=plan.id, result="success", metadata_json={"account_id": account.id, "version": plan.version, "revision_version": version}))


def _resolve_current_password(session_id: str) -> str | None:
    """Return the account's stored password to fill the current-password step of
    the Google password-change form. Unlike auto-login, credential automation
    needs the old password regardless of the BROWSER_AUTO_LOGIN flag."""
    db = SessionLocal()
    try:
        session = db.get(BrowserSession, session_id)
        if not session or not session.account or not session.account.secret:
            return None
        payload = SecretCipher().decrypt(session.account.secret.encrypted_payload)
        password = str((payload or {}).get("password") or "").strip()
        return password or None
    except Exception:  # noqa: BLE001 - never break a session over missing secrets
        logger.exception("could not resolve current password for session %s", session_id)
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


def _auto_persist_credential_change(db: Session, session: BrowserSession) -> None:
    """Store the automated password/2FA material the moment the observer reports
    Google accepted the change, so the account vault — and therefore the
    Credentials page — shows the new password without waiting for the operator's
    manual confirm. Falls back silently when material is incomplete so the
    manual confirm path still applies."""
    if not session.account_id:
        return
    plan = db.scalar(select(CredentialChangePlan).where(CredentialChangePlan.organization_id == session.organization_id, CredentialChangePlan.session_id == session.id, CredentialChangePlan.status == "READY_FOR_OPERATOR").order_by(CredentialChangePlan.started_at.desc().nullslast()).limit(1))
    if plan is None:
        return
    encrypted = (plan.configuration_json or {}).get("material_encrypted")
    if not encrypted and session.metadata_json:
        encrypted = (session.metadata_json or {}).get("credential_change_pending")
    if not encrypted:
        return
    try:
        pending = SecretCipher().decrypt(encrypted) or {}
    except Exception:  # noqa: BLE001 - leave the manual confirm as the fallback
        logger.exception("could not decrypt automated credential material for session %s", session.id)
        return
    if not isinstance(pending, dict):
        return
    password = str(pending.get("password") or "").strip()
    two_fa = str(pending.get("2fa") or "").strip()
    recovery = str(pending.get("recovery") or "").strip()
    if plan.password_requested and not password:
        return
    if plan.two_fa_requested and not two_fa:
        return
    if plan.recovery_requested and not recovery:
        pool: list[str] = []
        if plan.recovery_pool_encrypted:
            try:
                pool = (SecretCipher().decrypt(plan.recovery_pool_encrypted) or {}).get("emails") or []
            except Exception:  # noqa: BLE001
                pool = []
        recovery = str(pool[0]).strip() if pool else ""
    account = db.scalar(select(GmailAccount).options(joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == session.organization_id, GmailAccount.id == session.account_id))
    if account is None:
        return
    payload = {"password": password if plan.password_requested else None, "recovery": recovery if plan.recovery_requested else None, "2fa": two_fa if plan.two_fa_requested else None}
    try:
        version = _rotate_account_credentials_in_db(db, account, payload, "system")
    except ImportValidationError:
        logger.exception("could not auto-store the automated credentials for session %s", session.id)
        return
    now = utcnow()
    plan.status = "STORED_ENCRYPTED"
    plan.confirmed_at = now
    plan.completed_at = now
    configuration = dict(plan.configuration_json or {})
    configuration.update({"google_change_confirmed": True, "revision_version": version, "autosaved_on_completion": True})
    configuration.pop("material_encrypted", None)
    plan.configuration_json = configuration
    if plan.session_id:
        pending_session = db.get(BrowserSession, plan.session_id)
        if pending_session is not None:
            metadata = dict(pending_session.metadata_json or {})
            metadata.pop("credential_change_pending", None)
            pending_session.metadata_json = metadata
    db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="credential_change_plan.confirmed", target_type="credential_change_plan", target_id=plan.id, result="success", metadata_json={"account_id": account.id, "version": plan.version, "revision_version": version, "secret_status": "encrypted", "reauth_required": True, "fields": (plan.configuration_json or {}).get("fields"), "password_rotated": plan.password_requested, "recovery_rotated": plan.recovery_requested, "two_fa_rotated": plan.two_fa_requested, "autosaved_on_completion": True}))


def _report_observer_event(session_id: str, event: str, detail: str = "", result: str = "success", event_metadata: dict | None = None) -> None:
    db = SessionLocal()
    job = None
    newsletter_job = None
    newsletter_reuse_requested = False
    newsletter_reuse_started = False
    try:
        session = db.get(BrowserSession, session_id)
        if not session:
            return
        event_metadata = event_metadata if isinstance(event_metadata, dict) else {}
        bulk_newsletter_task = bool((session.metadata_json or {}).get("newsletter_task_id"))
        terminal_events = {"login.failed", "reuse.failed", "contacts.failed", "contacts.imported", "labels_markers.failed", "labels_markers.completed", "security.failed", "security.completed", "addons.failed", "addons.completed", "mail.failed", "mail.completed", "alerts.failed", "alerts.completed", "credential_change.failed", "credential_change.completed", "account_preferences.failed", "account_preferences.completed", "newsletter.signup.failed", "newsletter.signup.submitted", "newsletter.signup.manual_review", "newsletter.signup.skipped", "newsletter.confirmation.failed", "newsletter.confirmation.confirmed", "newsletter.confirmation.skipped"}
        # A bulk challenge or confirmation review also releases its browser
        # slot. Standalone anti-bot challenges remain available to an operator.
        if bulk_newsletter_task:
            terminal_events.update({"newsletter.signup.blocked_antibot", "newsletter.confirmation.manual_review"})
        if event.startswith("newsletter.signup."):
            metadata = dict(session.metadata_json or {})
            subscription_id = metadata.get("newsletter_subscription_id")
            subscription = db.get(NewsletterSubscription, subscription_id) if subscription_id else None
            result_map = {"newsletter.signup.submitted": ("SUBMITTED", None), "newsletter.signup.failed": ("FAILED", detail[:500] or "Signup failed"), "newsletter.signup.manual_review": ("MANUAL_REVIEW", detail[:500] or "Manual review required"), "newsletter.signup.blocked_antibot": ("MANUAL_REVIEW", "BLOCKED_ANTIBOT: human action or an approved integration is required"), "newsletter.signup.skipped": ("SKIPPED", detail[:500] or None)}
            state, error = result_map.get(event, ("PENDING", None))
            if subscription is not None:
                subscription.state = state
                subscription.last_error = error
                subscription.last_checked_at = utcnow()
                if state == "SUBMITTED":
                    subscription.submitted_at = utcnow()
            metadata["newsletter_result"] = state
            if event == "newsletter.signup.blocked_antibot":
                metadata["newsletter_block_code"] = "BLOCKED_ANTIBOT"
            session.metadata_json = metadata
            if event == "newsletter.signup.submitted" and event_metadata.get("newsletter_skill"):
                from .newsletter_service import record_newsletter_skill

                try:
                    # Skill learning is secondary to the signup/task outcome.
                    # Keep a failed skill write inside a savepoint so it can
                    # never prevent the terminal task state from committing.
                    with db.begin_nested():
                        record_newsletter_skill(
                            db,
                            session.organization_id,
                            str(metadata.get("newsletter_id") or ""),
                            str(metadata.get("newsletter_profile_id") or "") or None,
                            event_metadata.get("newsletter_skill"),
                        )
                except Exception:  # noqa: BLE001 - learning must not strand a completed signup
                    logger.exception("newsletter skill could not be persisted for session %s", session.id)
            skill_id = metadata.get("learned_skill_id")
            if skill_id:
                from .newsletter_service import mark_newsletter_skill_validated, record_newsletter_skill_failure

                skill = db.get(NewsletterAutomationSkill, str(skill_id))
                if skill is not None:
                    if event == "newsletter.skill.validated":
                        mark_newsletter_skill_validated(db, skill, 1.0)
                    elif event == "newsletter.signup.submitted":
                        skill.success_count += 1
                        skill.consecutive_failures = 0
                        skill.status = "ACTIVE"
                        skill.validation_confidence = 1.0
                        skill.last_success_at = utcnow()
                        skill.last_validated_at = utcnow()
                    elif event in {"newsletter.signup.failed", "newsletter.signup.manual_review", "newsletter.signup.blocked_antibot"} and metadata.get("execution_mode") == SKILL_REPLAY:
                        record_newsletter_skill_failure(db, skill, structural=event == "newsletter.signup.blocked_antibot")
            profile_id = metadata.get("newsletter_profile_id")
            if profile_id:
                profile = db.get(NewsletterAutomationProfile, profile_id)
                if profile is not None:
                    if state == "SUBMITTED":
                        profile.last_success_at = utcnow()
                    elif state in {"FAILED", "MANUAL_REVIEW"}:
                        profile.last_failure_at = utcnow()
        if event == "newsletter.skill.validated":
            metadata = dict(session.metadata_json or {})
            skill_id = metadata.get("learned_skill_id")
            if skill_id:
                from .newsletter_service import mark_newsletter_skill_validated

                skill = db.get(NewsletterAutomationSkill, str(skill_id))
                if skill is not None:
                    mark_newsletter_skill_validated(db, skill, 1.0)
            session.metadata_json = metadata
        if event.startswith("newsletter.confirmation."):
            metadata = dict(session.metadata_json or {})
            subscription_id = metadata.get("newsletter_subscription_id")
            subscription = db.get(NewsletterSubscription, subscription_id) if subscription_id else None
            result_map = {
                "newsletter.confirmation.waiting": ("WAITING_CONFIRMATION", None),
                "newsletter.confirmation.found": ("WAITING_CONFIRMATION", None),
                "newsletter.confirmation.opened": ("WAITING_CONFIRMATION", None),
                "newsletter.confirmation.confirmed": ("CONFIRMED", None),
                "newsletter.confirmation.failed": ("FAILED", detail[:500] or "Confirmation failed"),
                "newsletter.confirmation.manual_review": ("MANUAL_REVIEW", detail[:500] or "Manual review required"),
                "newsletter.confirmation.skipped": ("SKIPPED", detail[:500] or None),
            }
            state, error = result_map.get(event, ("WAITING_CONFIRMATION", None))
            if subscription is not None:
                subscription.state = state
                subscription.last_error = error
                subscription.last_checked_at = utcnow()
                if state == "CONFIRMED":
                    subscription.confirmed_at = utcnow()
            metadata["newsletter_result"] = state
            session.metadata_json = metadata
            profile_id = metadata.get("newsletter_profile_id")
            if profile_id:
                profile = db.get(NewsletterAutomationProfile, profile_id)
                if profile is not None:
                    if state == "CONFIRMED":
                        profile.last_success_at = utcnow()
                    elif state in {"FAILED", "MANUAL_REVIEW"}:
                        profile.last_failure_at = utcnow()
        if event.startswith("newsletter."):
            # Bulk newsletter tasks consume the same observer facts as the
            # single-account test. Keep the task lease and durable outcome in
            # sync without duplicating browser-page logic here.
            try:
                from .newsletter_service import record_newsletter_profile_test_result, sync_newsletter_task_for_session

                record_newsletter_profile_test_result(db, session, event, detail)
                sync_newsletter_task_for_session(db, session, event, detail)
                task_id = (session.metadata_json or {}).get("newsletter_task_id")
                if task_id:
                    NewsletterJobModel, NewsletterTaskModel = _newsletter_models()
                    task = db.get(NewsletterTaskModel, str(task_id))
                    if task is not None:
                        newsletter_job = db.get(NewsletterJobModel, task.job_id)
                        if (
                            event in {"newsletter.signup.submitted", "newsletter.signup.failed", "newsletter.signup.manual_review", "newsletter.signup.skipped"}
                            and (session.metadata_json or {}).get("newsletter_session_reuse_enabled")
                            and task.state in {"SUBMITTED", "FAILED", "MANUAL_REVIEW", "SKIPPED"}
                        ):
                            next_task = db.scalar(
                                select(NewsletterTaskModel.id)
                                .where(
                                    NewsletterTaskModel.job_id == task.job_id,
                                    NewsletterTaskModel.account_id == task.account_id,
                                    NewsletterTaskModel.state == "QUEUED",
                                )
                                .order_by(NewsletterTaskModel.created_at, NewsletterTaskModel.id)
                                .limit(1)
                            )
                            newsletter_reuse_requested = bool(next_task)
            except Exception:  # noqa: BLE001 - task bookkeeping must not break session finalization
                logger.exception("could not synchronize newsletter task for session %s", session.id)
        if event in {"newsletter.signup.manual_review", "newsletter.signup.blocked_antibot", "newsletter.confirmation.manual_review"} and session.state in ACTIVE_SESSION_STATES:
            session.state = "NEEDS_ATTENTION"
            session.login_state = "NEWSLETTER_MANUAL_REVIEW" if event.startswith("newsletter.signup") else "NEWSLETTER_CONFIRMATION_MANUAL_REVIEW"
            session.failure_reason = detail[:300] or "Manual review required"
            session.last_event_at = utcnow()
        if event == "mail.review_ready" and session.state in ACTIVE_SESSION_STATES:
            # Leave the authenticated browser open for the employee. This
            # action opens a matched message only; it never follows a link.
            session.state = "NEEDS_ATTENTION"
            session.login_state = "MAIL_WORKFLOW_REVIEW_READY"
            session.failure_reason = detail[:300] or "A matched email is open for operator review"
            session.last_event_at = utcnow()
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
            session.state = "RUNNING" if newsletter_reuse_requested else "CLOSED"
            if event == "contacts.imported":
                session.login_state = "CONTACTS_IMPORTED"
            elif event == "labels_markers.completed":
                session.login_state = "LABELS_MARKERS_COMPLETED"
            elif event == "security.completed":
                session.login_state = "SECURITY_COMPLETED"
            elif event == "addons.completed":
                session.login_state = "ADDONS_COMPLETED"
            elif event == "addons.failed":
                session.login_state = "ADDONS_FAILED"
            elif event == "mail.completed":
                session.login_state = "MAIL_WORKFLOW_COMPLETED"
            elif event == "mail.failed":
                session.login_state = "MAIL_WORKFLOW_FAILED"
            elif event == "alerts.completed":
                session.login_state = "ALERTS_COMPLETED"
            elif event == "alerts.failed":
                session.login_state = "ALERTS_FAILED"
            elif event == "credential_change.completed":
                session.login_state = "CREDENTIAL_CHANGE_COMPLETED"
            elif event == "credential_change.failed":
                session.login_state = "CREDENTIAL_CHANGE_FAILED"
            elif event == "account_preferences.completed":
                session.login_state = "ACCOUNT_PREFERENCES_COMPLETED"
            elif event == "account_preferences.failed":
                session.login_state = "ACCOUNT_PREFERENCES_FAILED"
            elif event == "login.failed":
                session.login_state = "FAILED"
            elif event == "reuse.failed":
                session.login_state = "FAILED"
            elif event == "newsletter.signup.submitted":
                session.login_state = "NEWSLETTER_SUBMITTED"
            elif event == "newsletter.signup.failed":
                session.login_state = "NEWSLETTER_FAILED"
            elif event in {"newsletter.signup.manual_review", "newsletter.signup.blocked_antibot"}:
                session.login_state = "NEWSLETTER_MANUAL_REVIEW"
            elif event == "newsletter.signup.skipped":
                session.login_state = "NEWSLETTER_SKIPPED"
            elif event == "newsletter.confirmation.confirmed":
                session.login_state = "NEWSLETTER_CONFIRMED"
            elif event == "newsletter.confirmation.failed":
                session.login_state = "NEWSLETTER_CONFIRMATION_FAILED"
            elif event == "newsletter.confirmation.manual_review":
                session.login_state = "NEWSLETTER_CONFIRMATION_MANUAL_REVIEW"
                if not bulk_newsletter_task:
                    session.state = "NEEDS_ATTENTION"
            elif event == "newsletter.confirmation.skipped":
                session.login_state = "NEWSLETTER_CONFIRMATION_SKIPPED"
            session.closed_at = None if newsletter_reuse_requested else now
            session.last_event_at = now
            session.failure_reason = None if event in {"contacts.imported", "labels_markers.completed", "security.completed", "addons.completed", "mail.completed", "alerts.completed", "credential_change.completed", "account_preferences.completed", "newsletter.signup.submitted", "newsletter.signup.skipped", "newsletter.confirmation.confirmed", "newsletter.confirmation.skipped"} else (detail[:300] or "Operation failed before completion")
            if event in {"contacts.imported", "labels_markers.completed", "security.completed", "addons.completed", "mail.completed", "alerts.completed", "credential_change.completed", "account_preferences.completed"}:
                session.account.login_status = "AUTHENTICATED"
                session.account.profile_status = "SAVED"
            elif event == "login.failed" and session.profile.authenticated_at is None:
                session.account.login_status = "NOT_STARTED"
                session.account.profile_status = "RESERVED"
            if not newsletter_reuse_requested:
                session.profile.status = "RESERVED"
                session.profile.lock_state = "UNLOCKED"
                session.profile.active_session_id = None
                session.profile.last_closed_at = now
            metadata = dict(session.metadata_json or {})
            metadata["browser_opened"] = True if newsletter_reuse_requested else False
            if newsletter_reuse_requested:
                metadata["newsletter_session_reuse_ready"] = True
                session.login_state = "NEWSLETTER_TASK_ADVANCING"
                session.failure_reason = None
            mail_config = (session.metadata_json or {}).get("mail_workflow")
            if event in {"mail.completed", "mail.failed"} and isinstance(mail_config, dict) and mail_config.get("action") == "confirmation_review":
                for key in ("confirmed_messages", "reviewed_message_count", "messages_needing_review"):
                    value = event_metadata.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        metadata[key] = max(0, value)
            metadata["terminal_result"] = {
                "contacts.imported": "CONTACTS_IMPORTED",
                "contacts.failed": "CONTACTS_IMPORT_FAILED",
                "labels_markers.completed": "LABELS_MARKERS_COMPLETED",
                "labels_markers.failed": "LABELS_MARKERS_FAILED",
                "security.completed": "SECURITY_COMPLETED",
                "security.failed": "SECURITY_FAILED",
                "addons.completed": "ADDONS_COMPLETED",
                "addons.failed": "ADDONS_FAILED",
                "mail.completed": "MAIL_WORKFLOW_COMPLETED",
                "mail.failed": "MAIL_WORKFLOW_FAILED",
                "alerts.completed": "ALERTS_COMPLETED",
                "alerts.failed": "ALERTS_FAILED",
                "credential_change.completed": "CREDENTIAL_CHANGE_COMPLETED",
                "credential_change.failed": "CREDENTIAL_CHANGE_FAILED",
                "account_preferences.completed": "ACCOUNT_PREFERENCES_COMPLETED",
                "account_preferences.failed": "ACCOUNT_PREFERENCES_FAILED",
                "login.failed": "LOGIN_FAILED",
                "reuse.failed": "REOPEN_FAILED",
                "newsletter.signup.submitted": "SUBMITTED",
                "newsletter.signup.failed": "FAILED",
                "newsletter.signup.manual_review": "MANUAL_REVIEW",
                "newsletter.signup.blocked_antibot": "MANUAL_REVIEW",
                "newsletter.signup.skipped": "SKIPPED",
                "newsletter.confirmation.confirmed": "CONFIRMED",
                "newsletter.confirmation.failed": "FAILED",
                "newsletter.confirmation.manual_review": "MANUAL_REVIEW",
                "newsletter.confirmation.skipped": "SKIPPED",
            }[event]
            session.metadata_json = metadata
            if event == "credential_change.completed":
                _auto_persist_credential_change(db, session)
            # Mail workflows are terminal observer events rather than page
            # observations. Finalize their queue item here so the next
            # account can claim the released concurrency slot immediately.
            job = _sync_job_item_for_session(db, session.id, session.state)
        elif event == "password.change_completed":
            _persist_password_leg(db, session)
        audit_metadata = {"detail": detail[:300], "account_id": session.account_id}
        if event_metadata:
            audit_metadata.update(event_metadata)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action=f"session.auto_login.{event}", target_type="browser_session", target_id=session.id, result=result, metadata_json=audit_metadata))
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
                "mail.completed": "MAIL_WORKFLOW_COMPLETED",
                "mail.failed": "MAIL_WORKFLOW_FAILED",
                "alerts.completed": "ALERTS_COMPLETED",
                "alerts.failed": "ALERTS_FAILED",
                "credential_change.completed": "CREDENTIAL_CHANGE_COMPLETED",
                "credential_change.failed": "CREDENTIAL_CHANGE_FAILED",
                "account_preferences.completed": "ACCOUNT_PREFERENCES_COMPLETED",
                "account_preferences.failed": "ACCOUNT_PREFERENCES_FAILED",
                "login.failed": "LOGIN_FAILED",
                "reuse.failed": "REOPEN_FAILED",
                "newsletter.signup.submitted": "SUBMITTED",
                "newsletter.signup.failed": "FAILED",
                "newsletter.signup.manual_review": "MANUAL_REVIEW",
                "newsletter.signup.blocked_antibot": "MANUAL_REVIEW",
                "newsletter.signup.skipped": "SKIPPED",
                "newsletter.confirmation.confirmed": "CONFIRMED",
                "newsletter.confirmation.failed": "FAILED",
                "newsletter.confirmation.manual_review": "MANUAL_REVIEW",
                "newsletter.confirmation.skipped": "SKIPPED",
            }[event]
            if not newsletter_reuse_requested:
                db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="session.closed", target_type="browser_session", target_id=session.id, result="success" if event in {"contacts.imported", "labels_markers.completed", "security.completed", "addons.completed", "mail.completed", "alerts.completed", "credential_change.completed", "account_preferences.completed", "newsletter.signup.submitted", "newsletter.confirmation.confirmed"} else "failure", metadata_json={"account_id": session.account_id, "reason": detail[:300], "terminal_result": terminal_result}))
        db.commit()
        if newsletter_job is not None:
            dispatch_result = _maybe_dispatch_newsletter_next(db, newsletter_job)
            if newsletter_reuse_requested:
                task = db.scalar(
                    select(_newsletter_models()[1]).where(
                        _newsletter_models()[1].job_id == newsletter_job.id,
                        _newsletter_models()[1].account_id == session.account_id,
                        _newsletter_models()[1].state == "PROCESSING",
                        _newsletter_models()[1].browser_session_id == session.id,
                    )
                )
                if task is None:
                    # The next task could not claim this browser (for example,
                    # its account lease was taken elsewhere). Let this observer
                    # close cleanly; the durable queue can launch a fresh one.
                    session.state = "CLOSED"
                    session.closed_at = utcnow()
                    session.last_event_at = utcnow()
                    session.login_state = f"NEWSLETTER_{terminal_result}"
                    session.profile.status = "RESERVED"
                    session.profile.lock_state = "UNLOCKED"
                    session.profile.active_session_id = None
                    session.profile.last_closed_at = utcnow()
                    metadata = dict(session.metadata_json or {})
                    metadata.update({"browser_opened": False, "newsletter_session_reuse_ready": False})
                    session.metadata_json = metadata
                    db.add(AuditEvent(id=_new_id("audit"), organization_id=session.organization_id, actor_id="system", action="session.closed", target_type="browser_session", target_id=session.id, result="success" if terminal_result in {"SUBMITTED", "SKIPPED"} else "attention", metadata_json={"account_id": session.account_id, "reason": "No queued site could reuse the browser session", "terminal_result": terminal_result}))
                    db.commit()
                else:
                    newsletter_reuse_started = True
        else:
            _maybe_dispatch_next(db, job)
        logger.info("session %s: auto-login %s %s", session_id, event, result)
        if event in {"contacts.failed", "labels_markers.failed", "security.failed", "addons.failed", "mail.failed", "alerts.failed", "credential_change.failed", "account_preferences.failed", "login.failed", "reuse.failed", "newsletter.signup.failed", "newsletter.signup.manual_review", "newsletter.signup.blocked_antibot", "newsletter.confirmation.failed", "newsletter.confirmation.manual_review"}:
            publish(db, session.organization_id, "operation_failure", f"Operation needs review · {session.account.email}", f"{session.account.email}: {event}\n{detail[:300]}")
        return newsletter_reuse_started
    finally:
        db.close()


def _prune_unsaved_profile_folder(session: BrowserSession) -> None:
    """Discard the on-disk profile unless the account is fully connected.

    A profile dir is only persisted once Google confirms the sign-in. Sessions
    that close before authentication remove their scratch user-data dir so no
    profile lingers for accounts that were never connected (previously saved
    profiles are always kept).
    """
    if (session.metadata_json or {}).get("ephemeral_browser"):
        remove_local_profile(session.profile_id)
        return
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
    deferred = 0
    # An account that already holds one of this wave's slots keeps the rest of
    # its queue behind it. One login job holds at most one item per account, so
    # this only guards against a re-queued duplicate appearing mid-wave.
    leased_accounts: set[str] = set()
    # Bound the scan so a large queue cannot spin a single tick.
    examined = 0
    examination_budget = max(int(job.concurrency_limit), 1) * 8 + 64
    while True:
        if examined >= examination_budget:
            break
        running = queue.running_count(job.id)
        if running >= job.concurrency_limit:
            break
        if queue.active_profile_count() >= settings.worker_max_active_profiles:
            break
        if job.queued_count <= 0:
            break
        examined += 1
        candidate = queue.pop_next(job.id)
        if not candidate:
            break
        item = db.get(LoginJobItem, candidate["item_id"])
        if not item or item.state != "QUEUED" or item.job_id != job.id:
            continue
        if item.account_id in leased_accounts:
            # Already running in this wave; let another account take the slot.
            deferred += 1
            continue
        token = locks.acquire(item.account_id, worker_id, "", settings.worker_lock_ttl * 1000)
        if not token:
            # This account is busy with another job or a live operator session.
            # Defer it briefly and keep filling the remaining slots instead of
            # abandoning every other account in the batch.
            queue.requeue(job.id, item.id, item.account_id, when=time.time() + 15.0)
            deferred += 1
            leased_accounts.add(item.account_id)
            continue
        leased_accounts.add(item.account_id)
        try:
            if job.operation_type == "mail":
                session = start_mail_workflow(db, organization_id, actor_id, item.account_id, dict(job.operation_config or {}))
            else:
                saved_profile = db.scalar(select(BrowserProfile).where(BrowserProfile.account_id == item.account_id))
                purpose = "reuse" if saved_profile and saved_profile.authenticated_at else "login"
                session = start_login_session(db, organization_id, actor_id, item.account_id, purpose=purpose)
        except BrowserLaunchError as exc:
            # Launch failed before anything was persisted: leave the item queued
            # and stop this wave so the worker does not hot-loop the same item.
            # The item stays eligible for the next tick.
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
            # Clear the queue mirror for a finished batch. A leftover running set
            # keeps running_count above the job's limit forever, which blocks any
            # later dispatch on that job id.
            _queue().clear(job.id)
            _queue().clear_running(job.id)
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


def _maybe_dispatch_newsletter_next(db: Session, job):
    """Release a newsletter task slot through the newsletter queue dispatcher."""
    if not job or job.status != "RUNNING":
        return
    try:
        from .newsletter_service import dispatch_newsletter_job

        return dispatch_newsletter_job(db, job.organization_id, API_WORKER_ID, job.id)
    except Exception:  # noqa: BLE001 - a closed browser callback must remain best effort
        db.rollback()
        logger.exception("could not dispatch the next newsletter task for job %s", getattr(job, "id", "unknown"))
        return None


def _sync_job_item_for_session(db: Session, session_id: str, session_state: str) -> LoginJob | None:
    item = db.scalar(select(LoginJobItem).where(LoginJobItem.session_id == session_id))
    if not item:
        return None
    job = db.get(LoginJob, item.job_id)
    session = db.get(BrowserSession, session_id)
    if item.state == "ATTENTION" and session_state == "RUNNING" and session is not None and session.login_state == "AUTHENTICATED" and job is not None and job.operation_type == "login":
        # The same open browser continued after a human completed its
        # challenge. Reconcile the previously paused batch outcome without
        # launching another session or changing another account's lease.
        item.state = "SUCCEEDED"
        item.paused_reason = None
        item.last_error = None
        item.completed_at = utcnow()
        _bump_job(db, job, attention_count=-1, succeeded_count=1)
        if job.queued_count == 0 and job.running_count == 0:
            job.status = "COMPLETED"
            job.completed_at = utcnow()
        return job
    if item.state != "RUNNING":
        return None
    # Pause the affected account whenever Google posed a challenge or the
    # worker hit an unknown page; the profile stays reserved for a human.
    if session_state == "RUNNING":
        if job is not None and job.operation_type != "login":
            item.paused_reason = None
            return job
        final_item_state = "SUCCEEDED"
        error = None
    elif session_state == "NEEDS_ATTENTION":
        item.paused_reason = (session.failure_reason if session is not None else None) or "Challenge or unknown page raised for operator attention"
        if session is not None and session.login_state == "VERIFICATION_REQUIRED":
            # Verification keeps the browser open and therefore continues to
            # occupy the queue slot. The observer resumes this same session
            # when the operator clears Google's prompt.
            return job
        if job is not None and job.operation_type != "login":
            return job
        return _finalize_item(db, item, "ATTENTION")
    elif session_state == "CLOSED":
        if session is not None and session.login_state in {"MAIL_WORKFLOW_COMPLETED", "MAIL_WORKFLOW_FAILED"}:
            return _finalize_item(db, item, "SUCCEEDED" if session.login_state == "MAIL_WORKFLOW_COMPLETED" else "FAILED", error=session.failure_reason)
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


def session_response(session: BrowserSession, browser_alive: bool | None = None) -> dict:
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
        "execution_mode": metadata.get("execution_mode"),
        "action": {"login": "Login", "reuse": "Reopen profile", "contacts": "Import contacts", "security": "Browser security", "labels_markers": "Labels & Markers", "addons": "Install Gmail add-ons", "mail": "Mail workflow", "alerts": "Google Alerts", "credential_change": "Change credentials", "account_preferences": "Account preferences", "newsletter": "Newsletter signup test", "newsletter_confirm": "Newsletter confirmation"}.get(purpose, purpose.replace("_", " ").title()),
        "operation_status": operation_status,
        "status_detail": status_detail,
        "duration_seconds": duration_seconds,
        "runtime_type": session.runtime_type,
        "browser_opened": bool(session.metadata_json.get("browser_opened", False)),
        "browser_alive": browser_alive if browser_alive is not None else (
            session.state in ACTIVE_SESSION_STATES
            and bool(session.metadata_json.get("browser_opened", False))
            and local_browser_is_alive(session.profile_id, session.id)
        ),
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
    successful_states = {"AUTHENTICATED", "CONTACTS_IMPORTED", "LABELS_MARKERS_COMPLETED", "SECURITY_COMPLETED", "ADDONS_COMPLETED", "MAIL_WORKFLOW_COMPLETED", "ALERTS_COMPLETED", "CREDENTIAL_CHANGE_COMPLETED", "ACCOUNT_PREFERENCES_COMPLETED", "NEWSLETTER_SUBMITTED", "NEWSLETTER_SKIPPED", "NEWSLETTER_CONFIRMED", "NEWSLETTER_CONFIRMATION_SKIPPED"}
    failed_states = {"ACCOUNT_NOT_FOUND", "FAILED", "LAUNCH_FAILED", "SECURITY_FAILED", "ADDONS_FAILED", "MAIL_WORKFLOW_FAILED", "ALERTS_FAILED", "CREDENTIAL_CHANGE_FAILED", "ACCOUNT_PREFERENCES_FAILED", "NEWSLETTER_FAILED", "NEWSLETTER_CONFIRMATION_FAILED"}
    interrupted_states = {"VERIFICATION_REQUIRED", "SECURITY_CHALLENGE", "CAPTCHA", "MFA_REQUIRED", "MAIL_WORKFLOW_REVIEW_READY", "NEWSLETTER_MANUAL_REVIEW", "NEWSLETTER_CONFIRMATION_MANUAL_REVIEW"}
    purpose = (session.metadata_json or {}).get("purpose", "login")
    if purpose in {"contacts", "labels_markers", "security", "addons", "mail", "alerts", "credential_change", "account_preferences", "newsletter", "newsletter_confirm"} and session.login_state == "AUTHENTICATED":
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


def list_sessions(db: Session, organization_id: str, account_id: str | None, limit: int = 25, group_id: str | None = None, latest_per_account: bool = False, purpose: str | None = None, outcome: str | None = None, visible_group_ids: set[str] | None = None) -> list[dict]:
    query = select(BrowserSession).options(joinedload(BrowserSession.account), joinedload(BrowserSession.profile)).where(BrowserSession.organization_id == organization_id).order_by(BrowserSession.created_at.desc()).limit(limit)
    if visible_group_ids is not None:
        query = query.where(BrowserSession.account_id.in_(select(GmailAccount.id).where(GmailAccount.group_id.in_(visible_group_ids or {"__no_access__"}))))
    if account_id:
        query = query.where(BrowserSession.account_id == account_id)
    if group_id:
        query = query.join(BrowserSession.account).where(GmailAccount.group_id == group_id)
    if latest_per_account:
        ranked = select(BrowserSession.id, func.row_number().over(partition_by=BrowserSession.account_id, order_by=(BrowserSession.created_at.desc(), BrowserSession.id.desc())).label("session_rank")).where(BrowserSession.organization_id == organization_id).subquery()
        query = query.join(ranked, BrowserSession.id == ranked.c.id).where(ranked.c.session_rank == 1)
    if purpose:
        query = query.where(BrowserSession.metadata_json["purpose"].as_string() == purpose)
    sessions = db.scalars(query).unique().all()
    candidate_profiles = {
        item.profile_id
        for item in sessions
        if item.state in ACTIVE_SESSION_STATES and bool((item.metadata_json or {}).get("browser_opened"))
    }
    live_profiles = local_browser_profiles_alive(candidate_profiles)
    rows = [session_response(item, browser_alive=item.profile_id in live_profiles) for item in sessions]
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


def list_browser_profiles(db: Session, organization_id: str, query_text: str | None = None, status_filter: str | None = None, limit: int = 100, visible_group_ids: set[str] | None = None) -> list[dict]:
    query = (
        select(BrowserProfile)
        .join(BrowserProfile.account)
        .options(joinedload(BrowserProfile.account))
        .where(BrowserProfile.organization_id == organization_id)
        .order_by(BrowserProfile.updated_at.desc())
        .limit(limit)
    )
    if visible_group_ids is not None:
        query = query.where(GmailAccount.group_id.in_(visible_group_ids or {"__no_access__"}))
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


def list_proxies(db: Session, organization_id: str, query_text: str | None = None, status_filter: str | None = None, group_id: str | None = None, visible_group_ids: set[str] | None = None) -> list[dict]:
    """Build a safe proxy inventory, including currently unassigned records."""
    account_query = select(GmailAccount).options(joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.lifecycle_status != "ARCHIVED").order_by(GmailAccount.email)
    if group_id:
        account_query = account_query.where(GmailAccount.group_id == group_id)
    if visible_group_ids is not None:
        account_query = account_query.where(GmailAccount.group_id.in_(visible_group_ids or {"__no_access__"}))
    accounts = db.scalars(account_query).all()
    observations = {item.id: item for item in db.scalars(select(ProxyInventory).where(ProxyInventory.organization_id == organization_id)).all()}
    grouped: dict[str, dict] = {}
    for observation in observations.values():
        grouped[observation.id] = {
            "id": observation.id,
            "label": observation.label,
            "proxy_group": observation.proxy_group,
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
        item = grouped.setdefault(account.proxy_id, {"id": account.proxy_id, "label": account.proxy_label, "proxy_group": observations.get(account.proxy_id).proxy_group if observations.get(account.proxy_id) else None, "region": account.proxy_region, "status": account.proxy_status, "account_count": 0, "authenticated_count": 0, "account_emails": [], "account_refs": [], **_proxy_inventory_item(observations.get(account.proxy_id))})
        item["status"] = account.proxy_status
        item["account_count"] += 1
        item["authenticated_count"] += int(account.login_status == "AUTHENTICATED" or bool(account.browser_profile and account.browser_profile.authenticated_at))
        item["account_emails"].append(account.email)
        item["account_refs"].append({"id": account.id, "email": account.email})
        if item["label"] is None and account.proxy_label:
            item["label"] = account.proxy_label
        if item["region"] is None and account.proxy_region:
            item["region"] = account.proxy_region
    if visible_group_ids is not None:
        # Operators can inspect only proxy records attached to accounts in
        # their assigned groups; unassigned inventory belongs to admins.
        grouped = {key: item for key, item in grouped.items() if item["account_count"] > 0}
    if query_text:
        needle = query_text.strip().lower()
        grouped = {key: item for key, item in grouped.items() if needle in " ".join([str(item.get("id") or ""), str(item.get("label") or ""), str(item.get("proxy_group") or ""), str(item.get("region") or ""), str(item.get("provider") or ""), str(item.get("country") or ""), *item["account_emails"]]).lower()}
    if status_filter:
        grouped = {key: item for key, item in grouped.items() if item["status"] == status_filter or item["test_status"] == status_filter}
    return list(grouped.values())


def create_proxy_group(db: Session, organization_id: str, actor_id: str, raw_name: str) -> dict:
    name = " ".join(raw_name.split())
    if not name or len(name) > 80:
        raise ImportValidationError("Proxy list names must contain 1–80 characters")
    existing = db.scalar(select(ProxyGroup).where(ProxyGroup.organization_id == organization_id, ProxyGroup.name == name))
    if existing:
        raise ImportValidationError("A proxy list with this name already exists")
    group = ProxyGroup(id=_new_id("proxy_group"), organization_id=organization_id, name=name)
    db.add(group)
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="proxy.group_created", target_type="proxy_group", target_id=name, result="success", metadata_json={"proxy_count": 0}))
    db.commit()
    return {"id": group.id, "name": group.name, "proxy_count": 0}


def list_proxy_groups(db: Session, organization_id: str) -> list[dict]:
    named = db.scalars(select(ProxyGroup).where(ProxyGroup.organization_id == organization_id).order_by(ProxyGroup.name)).all()
    counts = dict(db.execute(select(ProxyInventory.proxy_group, func.count(ProxyInventory.id)).where(ProxyInventory.organization_id == organization_id, ProxyInventory.proxy_group.is_not(None)).group_by(ProxyInventory.proxy_group)).all())
    groups = {group.name: {"id": group.id, "name": group.name, "proxy_count": int(counts.pop(group.name, 0))} for group in named}
    for name, count in counts.items():
        groups[name] = {"id": f"legacy:{name}", "name": name, "proxy_count": int(count)}
    return sorted(groups.values(), key=lambda group: group["name"].casefold())


def update_proxy_group(db: Session, organization_id: str, actor_id: str, proxy_ids: list[str], group_name: str | None) -> dict:
    if not proxy_ids or len(proxy_ids) != len(set(proxy_ids)):
        raise ImportValidationError("Select one or more distinct proxy endpoints")
    name = " ".join(group_name.split()) if group_name else None
    if name and len(name) > 80:
        raise ImportValidationError("Proxy group names must be 80 characters or fewer")
    records = db.scalars(select(ProxyInventory).where(ProxyInventory.organization_id == organization_id, ProxyInventory.id.in_(proxy_ids)).with_for_update()).all()
    if len(records) != len(proxy_ids):
        raise ImportValidationError("One or more selected proxies are not in the inventory")
    for record in records:
        record.proxy_group = name
    if name and not db.scalar(select(ProxyGroup.id).where(ProxyGroup.organization_id == organization_id, ProxyGroup.name == name)):
        db.add(ProxyGroup(id=_new_id("proxy_group"), organization_id=organization_id, name=name))
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="proxy.group_membership_updated", target_type="proxy_group", target_id=name or "ungrouped", result="success", metadata_json={"proxy_ids": sorted(proxy_ids), "proxy_count": len(records), "group_name": name}))
    db.commit()
    return {"group_name": name, "updated_count": len(records), "proxy_ids": [record.id for record in records]}


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


def import_proxy_inventory(db: Session, organization_id: str, actor_id: str, raw_lines: str, group_name: str | None = None) -> dict:
    """Store a bounded set of unassigned proxy endpoints without exposing secrets.

    Imports are deliberately additive. A connection with an endpoint already
    known to inventory or an account is reported as a duplicate instead of
    replacing its encrypted credentials.
    """

    lines = [(line_number, raw.strip()) for line_number, raw in enumerate(raw_lines.splitlines(), start=1) if raw.strip()]
    if not lines:
        raise ImportValidationError("Paste at least one proxy connection")
    if len(lines) > 500:
        raise ImportValidationError("Import up to 500 proxy connections at a time")

    group_name = " ".join(group_name.split()) if group_name else None
    if group_name and len(group_name) > 80:
        raise ImportValidationError("Proxy group names must be 80 characters or fewer")
    if group_name:
        group_record_exists = db.scalar(select(ProxyGroup.id).where(ProxyGroup.organization_id == organization_id, func.lower(ProxyGroup.name) == group_name.casefold()))
        legacy_group_exists = db.scalar(select(ProxyInventory.id).where(ProxyInventory.organization_id == organization_id, ProxyInventory.proxy_group == group_name).limit(1))
    else:
        group_record_exists = legacy_group_exists = True

    existing_proxy_ids = set(db.scalars(select(ProxyInventory.id).where(ProxyInventory.organization_id == organization_id)).all())
    existing_proxy_ids.update(
        proxy_id
        for proxy_id in db.scalars(
            select(GmailAccount.proxy_id).where(
                GmailAccount.organization_id == organization_id,
                GmailAccount.proxy_id.is_not(None),
                GmailAccount.lifecycle_status != "ARCHIVED",
            )
        ).all()
        if proxy_id
    )
    seen_proxy_ids: set[str] = set()
    rows: list[dict] = []
    imported = duplicates = invalid = 0

    for line_number, raw_line in lines:
        parts = [part.strip() for part in raw_line.split("|")]
        if len(parts) > 3 or not parts[0]:
            invalid += 1
            rows.append({"line_number": line_number, "status": "INVALID", "message": "Use connection, optional label, and optional region separated by |", "proxy_id": None})
            continue
        connection = parts[0]
        label = parts[1] or None if len(parts) > 1 else None
        region = parts[2] or None if len(parts) > 2 else None
        if label is not None and len(label) > 160:
            invalid += 1
            rows.append({"line_number": line_number, "status": "INVALID", "message": "Label must be 160 characters or fewer", "proxy_id": None})
            continue
        if region is not None and len(region) > 80:
            invalid += 1
            rows.append({"line_number": line_number, "status": "INVALID", "message": "Region must be 80 characters or fewer", "proxy_id": None})
            continue
        try:
            proxy = parse_proxy(connection)
        except ProxyValidationError as exc:
            invalid += 1
            rows.append({"line_number": line_number, "status": "INVALID", "message": str(exc), "proxy_id": None})
            continue
        if proxy is None:
            invalid += 1
            rows.append({"line_number": line_number, "status": "INVALID", "message": "Enter a proxy connection", "proxy_id": None})
            continue
        if proxy.proxy_id in existing_proxy_ids or proxy.proxy_id in seen_proxy_ids:
            duplicates += 1
            rows.append({"line_number": line_number, "status": "DUPLICATE", "message": "This endpoint already exists and was not changed", "proxy_id": proxy.proxy_id})
            continue

        observation = _upsert_proxy_inventory(db, organization_id, proxy, label, region)
        observation.proxy_group = group_name
        db.add(AuditEvent(
            id=_new_id("audit"),
            organization_id=organization_id,
            actor_id=actor_id,
            action="proxy.imported",
            target_type="proxy_inventory",
            target_id=observation.id,
            result="success",
            metadata_json={"proxy_id": observation.id, "proxy_authentication": "authenticated" if proxy.authenticated else "anonymous", "source": "bulk_import", "proxy_group": group_name},
        ))
        seen_proxy_ids.add(proxy.proxy_id)
        imported += 1
        rows.append({"line_number": line_number, "status": "IMPORTED", "message": f"Stored in {group_name}" if group_name else "Stored as ungrouped inventory", "proxy_id": observation.id})

    if imported and group_name:
        if not group_record_exists and not legacy_group_exists:
            db.add(ProxyGroup(id=_new_id("proxy_group"), organization_id=organization_id, name=group_name))

    db.commit()
    return {"imported": imported, "duplicates": duplicates, "invalid": invalid, "rows": rows}


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


def distribute_proxies_to_group(db: Session, organization_id: str, actor_id: str, group_id: str, proxy_ids: list[str], accounts_per_proxy: int | None, mode: str = "limit") -> dict:
    """Assign every active group account to the least loaded selected proxy.

    Existing assignments outside the group consume capacity. Validate the
    entire plan before changing encrypted account secrets so an undersized or
    unhealthy selection cannot leave a partially assigned group.
    """
    if not proxy_ids or len(proxy_ids) != len(set(proxy_ids)):
        raise ImportValidationError("Select one or more distinct proxy endpoints")
    if mode not in {"equal", "limit"}:
        raise ImportValidationError("Choose an equal spread or a per-proxy limit")
    if mode == "limit" and (accounts_per_proxy is None or not 1 <= accounts_per_proxy <= 300):
        raise ImportValidationError("Accounts per proxy must be between 1 and 300")
    group = db.scalar(select(AccountGroup).where(AccountGroup.organization_id == organization_id, AccountGroup.id == group_id))
    if group is None:
        raise ImportValidationError("Account group not found")
    records = db.scalars(select(ProxyInventory).where(ProxyInventory.organization_id == organization_id, ProxyInventory.id.in_(proxy_ids))).all()
    if len(records) != len(proxy_ids):
        raise ImportValidationError("One or more selected proxies are not in the inventory")
    if any(record.test_status != "HEALTHY" for record in records):
        raise ImportValidationError("Test the selected proxies first; all must be healthy")
    configs = {record.id: _proxy_config_for_id(db, organization_id, record.id)[0] for record in records}
    targets = db.scalars(select(GmailAccount).options(joinedload(GmailAccount.secret), joinedload(GmailAccount.browser_profile)).where(GmailAccount.organization_id == organization_id, GmailAccount.group_id == group_id, GmailAccount.lifecycle_status != "ARCHIVED").order_by(GmailAccount.email).with_for_update()).all()
    if not targets:
        raise ImportValidationError("The selected group has no active accounts")
    if any(account.browser_profile and account.browser_profile.lock_state == "LOCKED" for account in targets):
        raise ImportValidationError("Close active sessions in this group before changing proxies")
    outside = db.execute(select(GmailAccount.proxy_id, func.count(GmailAccount.id)).where(GmailAccount.organization_id == organization_id, GmailAccount.group_id != group_id, GmailAccount.lifecycle_status != "ARCHIVED", GmailAccount.proxy_id.in_(proxy_ids)).group_by(GmailAccount.proxy_id)).all()
    loads = {proxy_id: 0 for proxy_id in proxy_ids}
    loads.update({proxy_id: count for proxy_id, count in outside})
    group_loads = {proxy_id: 0 for proxy_id in proxy_ids}
    if mode == "limit" and sum(max(0, accounts_per_proxy - count) for count in loads.values()) < len(targets):
        raise ImportValidationError("Selected proxies do not have enough capacity for every account in this group")
    plan: list[tuple[GmailAccount, ProxyInventory]] = []
    by_id = {record.id: record for record in records}
    for account in targets:
        eligible = proxy_ids if mode == "equal" else [proxy_id for proxy_id in proxy_ids if loads[proxy_id] < accounts_per_proxy]
        selected = min(eligible, key=lambda proxy_id: (group_loads[proxy_id], loads[proxy_id], proxy_id))
        plan.append((account, by_id[selected]))
        loads[selected] += 1
        group_loads[selected] += 1
    planned_account_ids = [account.id for account, _record in plan]
    if len(planned_account_ids) != len(set(planned_account_ids)) or len(plan) != len(targets):
        raise ImportValidationError("Distribution plan did not produce exactly one proxy per group account")
    changed = 0
    for account, record in plan:
        if account.proxy_id != record.id:
            changed += 1
        proxy = configs[record.id]
        payload = _safe_secret_payload(account)
        payload["proxy"] = proxy.encrypted_payload()
        encrypted = SecretCipher().encrypt(payload)
        if account.secret:
            account.secret.encrypted_payload = encrypted
        else:
            account.secret = AccountSecret(id=_new_id("secret"), account_id=account.id, secret_ref=f"secret://account/{account.id}/primary", encrypted_payload=encrypted)
        account.proxy_id = record.id
        account.proxy_label = record.label or proxy.display_endpoint
        account.proxy_region = record.region
        account.proxy_status = "ASSIGNED"
    db.flush()
    persisted_assignments = dict(
        db.execute(
            select(GmailAccount.id, GmailAccount.proxy_id).where(
                GmailAccount.organization_id == organization_id,
                GmailAccount.id.in_(planned_account_ids),
            )
        ).all()
    )
    expected_assignments = {account.id: record.id for account, record in plan}
    if persisted_assignments != expected_assignments or sum(group_loads.values()) != len(targets):
        db.rollback()
        raise ImportValidationError("Distribution did not save a unique proxy assignment for every group account")
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="proxy.group_distributed", target_type="account_group", target_id=group_id, result="success", metadata_json={"proxy_ids": sorted(proxy_ids), "mode": mode, "accounts_per_proxy": accounts_per_proxy if mode == "limit" else None, "account_count": len(targets), "changed_count": changed, "distribution": group_loads}))
    db.commit()
    return {"group_id": group_id, "account_count": len(targets), "changed_count": changed, "mode": mode, "accounts_per_proxy": accounts_per_proxy if mode == "limit" else None, "distribution": group_loads}


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


def start_login_session(db: Session, organization_id: str, actor_id: str, account_id: str, purpose: str = "login", contacts_csv_path: str | None = None, mail_workflow: dict[str, object] | None = None, alerts_topics: list[str] | None = None, newsletter_config: dict[str, object] | None = None, account_preferences: dict[str, object] | None = None) -> BrowserSession:
    # The API and bulk UI can issue two starts for the same account at nearly
    # the same time. Serialise only that account; unrelated accounts in a bulk
    # run remain concurrent.
    with _account_start_lock(account_id):
        return _start_login_session_locked(
            db,
            organization_id,
            actor_id,
            account_id,
            purpose=purpose,
            contacts_csv_path=contacts_csv_path,
            mail_workflow=mail_workflow,
            alerts_topics=alerts_topics,
            newsletter_config=newsletter_config,
            account_preferences=account_preferences,
        )


def start_newsletter_signup_test(
    db: Session,
    organization_id: str,
    actor_id: str,
    newsletter_id: str,
    account_id: str,
    profile_id: str | None,
    job_id: str | None = None,
    task_id: str | None = None,
    test_identity: dict[str, str] | None = None,
    signup_password: str | None = None,
    auto_confirm: bool = False,
    generate_identity: bool = True,
    headless_browser: bool | None = None,
    reuse_session_id: str | None = None,
) -> BrowserSession:
    """Start one signup against one saved Gmail profile.

    Standalone tests can chain into a separate Gmail confirmation session after
    the signup browser closes. Bulk jobs use their durable task dispatcher for
    the same confirmation phase. A learning run may omit ``profile_id`` and
    starts directly from the newsletter's configured signup URL.

    Optional signup fields fall back to a stable synthetic profile per account
    (see ``newsletter_identity``). Operator-approved and explicitly requested
    values always take precedence over a generated placeholder.
    """
    with _account_start_lock(account_id):
        newsletter = db.scalar(select(Newsletter).where(Newsletter.organization_id == organization_id, Newsletter.id == newsletter_id))
        if not newsletter:
            raise ImportValidationError("Newsletter not found")
        if newsletter.status == "DISABLED":
            raise ImportValidationError("Disabled newsletters cannot be tested")
        profile = None
        config: dict[str, object] = {}
        from .newsletter_service import begin_newsletter_profile_test, get_active_newsletter_skill, mark_newsletter_skill_used

        if profile_id:
            profile = db.scalar(select(NewsletterAutomationProfile).where(NewsletterAutomationProfile.organization_id == organization_id, NewsletterAutomationProfile.id == profile_id, NewsletterAutomationProfile.newsletter_id == newsletter_id))
            if not profile:
                raise ImportValidationError("Automation profile not found for this newsletter")
            if profile.status not in NEWSLETTER_SIGNABLE_PROFILE_STATUSES:
                raise ImportValidationError("Choose a candidate, tested, or active automation profile")
            begin_newsletter_profile_test(db, organization_id, actor_id, profile)
            config = dict(profile.configuration_json or {})
        else:
            # A discovered form is already the conservative route: discovery
            # captures it and an operator reviews it. Requiring the adaptive
            # agent and its four workspace gates before a first run would make
            # DETERMINISTIC unreachable, which inverts the documented order
            # (SKILL_REPLAY > DETERMINISTIC > AGENT_LEARNING > MANUAL). Fall back
            # to the newsletter's newest signable profile instead, and only look
            # to the agent when there is no configuration to run at all.
            profile = db.scalar(
                select(NewsletterAutomationProfile)
                .where(
                    NewsletterAutomationProfile.organization_id == organization_id,
                    NewsletterAutomationProfile.newsletter_id == newsletter_id,
                    NewsletterAutomationProfile.status.in_(NEWSLETTER_SIGNABLE_PROFILE_STATUSES),
                )
                .order_by(NewsletterAutomationProfile.version.desc())
            )
            if profile is not None:
                begin_newsletter_profile_test(db, organization_id, actor_id, profile)
                config = dict(profile.configuration_json or {})
        learned_skill = get_active_newsletter_skill(db, organization_id, newsletter_id)
        if learned_skill is not None and not isinstance(getattr(learned_skill, "skill_json", None), dict):
            learned_skill = None
        if learned_skill:
            config["learned_skill"] = dict(learned_skill.skill_json or {})
            config["learned_skill_id"] = learned_skill.id
            mark_newsletter_skill_used(db, learned_skill)
        runtime_agent = _newsletter_agent_runtime_settings(db, organization_id)
        agent_ready = bool(
            runtime_agent.get("enabled")
            and runtime_agent.get("api_key_configured")
            and runtime_agent.get("learning_mode")
            and runtime_agent.get("live_execution_enabled")
            and not runtime_agent.get("shadow_mode")
            and any(entry.get("enabled") and entry.get("model") for entry in runtime_agent.get("agent_chain", []))
        )
        execution_mode = choose_newsletter_execution_mode(
            # An automatically discovered profile supplies selectors but must
            # not silently turn an explicitly enabled learning run static.
            profile_id=profile.id if profile_id else None,
            has_active_skill=bool(learned_skill),
            agent_ready=agent_ready,
            live_form_enabled=get_settings().newsletter_live_form_enabled,
        )
        # The run's shape is recorded once and reused for the config, the audit
        # event and the response, so the three can never disagree about how a
        # session was actually driven.
        signup_mode = "ADAPTIVE_LEARNING" if execution_mode == AGENT_LEARNING else ("PROFILE" if profile else "LIVE_FORM")
        if execution_mode == MANUAL:
            adaptive_requirements = {
                "agent_enabled": bool(runtime_agent.get("configured_enabled")),
                "provider_key_configured": bool(runtime_agent.get("api_key_configured")),
                "learning_mode": bool(runtime_agent.get("learning_mode")),
                "live_execution_enabled": bool(runtime_agent.get("live_execution_enabled")),
                "shadow_mode": bool(runtime_agent.get("shadow_mode")),
                # The last-resort browser-use resolver needs its own switch and
                # an OpenAI key, so report it alongside the agent gates rather
                # than letting it look like a silent failure later.
                "browser_use_enabled": bool((runtime_agent.get("browser_use_settings") or {}).get("browser_use_enabled")),
                # The declared gates can all read true while the chain is empty
                # or holds a provider with no model, which is the remaining way
                # to be blocked with no visible cause.
                "agent_chain_ready": any(entry.get("enabled") and entry.get("model") for entry in runtime_agent.get("agent_chain", [])),
                # Reaching this branch now also means live-form runs are
                # switched off, which is otherwise invisible in the state above.
                "live_form_enabled": bool(get_settings().newsletter_live_form_enabled),
            }
            state = ", ".join(f"{key}={str(value).lower()}" for key, value in adaptive_requirements.items())
            # Reached only when this newsletter has no signable profile, the
            # agent is not ready, and live-form runs are switched off. Any one
            # of those three is enough to make this run possible.
            raise ImportValidationError(f"No automation profile is available for this newsletter, and the adaptive agent is not ready ({state}). Inspect the public signup page to save a candidate form profile, or finish the agent setup in Settings.")
        signup_url = str(config.get("signup_url") or newsletter.signup_url or newsletter.website_url or "").strip()
        if not signup_url:
            raise ImportValidationError("This newsletter has no website or signup URL configured")
        try:
            signup_url = validate_newsletter_runtime_url(signup_url)
        except NewsletterDiscoveryError as exc:
            raise ImportValidationError("The signup URL is not a safe public http(s) URL") from exc
        account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
        if not account:
            raise ImportValidationError("Account not found")
        if account.lifecycle_status == "ARCHIVED":
            raise ImportValidationError("Archived accounts cannot run signup tests")
        external_recipient = account.account_type == "EXTERNAL_RECIPIENT"
        if external_recipient:
            # External recipients have no Gmail session and cannot be
            # confirmed through a mailbox owned by the workspace.
            auto_confirm = False
            # Direct external signup tests use the same isolated headless
            # default as external bulk jobs unless the caller explicitly
            # requests a visible browser for troubleshooting.
            if headless_browser is None:
                headless_browser = True
        else:
            headless_browser = False
        saved_identity = {} if external_recipient else (get_newsletter_account_identity(db, organization_id, account_id).get("values") or {})
        subscription = db.scalar(select(NewsletterSubscription).where(NewsletterSubscription.organization_id == organization_id, NewsletterSubscription.account_id == account_id, NewsletterSubscription.newsletter_id == newsletter_id))
        if subscription and subscription.state == "CONFIRMED":
            raise ImportValidationError("This account is already confirmed for this newsletter")
        if subscription is None:
            subscription = NewsletterSubscription(id=_new_id("nls"), organization_id=organization_id, account_id=account_id, newsletter_id=newsletter_id)
            db.add(subscription)
            db.flush()
        publisher_password = _publisher_signup_password(subscription, signup_password)
        if publisher_password:
            config["signup_password"] = publisher_password
        subscription.state = "PENDING"
        subscription.last_error = None
        subscription.last_checked_at = utcnow()
        # Keep approved extra form values in the in-memory observer payload
        # only. The immutable profile remains selector/configuration data and
        # the session metadata never stores these values.
        # Generated placeholders form the base layer so a signup can run
        # without an operator typing optional values first. Precedence is
        # requested > saved > generated: enabling generation can never
        # overwrite a value a human approved.
        generated_identity: dict[str, str] = {}
        if generate_identity:
            organization = db.scalar(select(Organization).where(Organization.id == organization_id))
            generated_identity = build_newsletter_identity(
                organization_id,
                account_id,
                org_name=getattr(organization, "name", None),
                account_email=account.email,
            )
        merged_identity = merge_newsletter_identity(generated_identity, saved_identity, test_identity)
        if merged_identity:
            config["test_identity"] = merged_identity
        config.update({
            "signup_url": signup_url,
            "account_email": account.email,
            "newsletter_id": newsletter_id,
            "newsletter_name": newsletter.name,
            "profile_id": profile.id if profile else "",
            "profile_version": str(profile.version) if profile else "",
            "signup_mode": signup_mode,
            "subscription_id": subscription.id,
            "newsletter_auto_confirm": bool(auto_confirm),
            "execution_mode": execution_mode,
            "newsletter_confirmation_required": bool(newsletter.confirmation_required),
            "external_recipient": external_recipient,
            "ephemeral_browser": external_recipient,
            "headless_browser": bool(headless_browser),
        })
        if job_id:
            config["job_id"] = job_id
        if task_id:
            config["task_id"] = task_id
        config["reuse_session_for_job"] = bool(job_id)
        if reuse_session_id:
            existing_session = db.scalar(
                select(BrowserSession).where(
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.id == reuse_session_id,
                    BrowserSession.account_id == account_id,
                    BrowserSession.state == "RUNNING",
                )
            )
            existing_observer = _observers.get(reuse_session_id)
            existing_metadata = dict(existing_session.metadata_json or {}) if existing_session else {}
            if (
                existing_session is None
                or existing_observer is None
                or not existing_metadata.get("newsletter_session_reuse_ready")
                or existing_metadata.get("newsletter_job_id") != job_id
                or not existing_observer.queue_newsletter_task_switch({**config, "agent_settings": _newsletter_agent_runtime_settings(db, organization_id)})
            ):
                raise AccountSessionConflictError("The existing newsletter browser is no longer ready for the next site")
            existing_metadata.update({
                "newsletter_id": newsletter_id,
                "newsletter_profile_id": profile.id if profile else None,
                "newsletter_profile_version": str(profile.version) if profile else "",
                "newsletter_signup_mode": signup_mode,
                "execution_mode": execution_mode,
                "learned_skill_id": config.get("learned_skill_id"),
                "newsletter_subscription_id": subscription.id,
                "newsletter_auto_confirm": bool(auto_confirm),
                "newsletter_confirmation_required": bool(newsletter.confirmation_required),
                "newsletter_result": "IN_PROGRESS",
                "newsletter_task_id": task_id,
                "newsletter_session_reuse_ready": False,
                "newsletter_session_reuse_enabled": True,
            })
            existing_session.metadata_json = existing_metadata
            existing_session.state = "RUNNING"
            existing_session.login_state = "NEWSLETTER_RUNNING"
            existing_session.failure_reason = None
            existing_session.closed_at = None
            session = existing_session
        else:
            session = _start_login_session_locked(db, organization_id, actor_id, account_id, purpose="newsletter", newsletter_config=config)
        subscription.last_checked_at = utcnow()
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="newsletter.signup.started", target_type="newsletter_subscription", target_id=subscription.id, result="success", metadata_json={"newsletter_id": newsletter_id, "account_id": account_id, "profile_version": profile.version if profile else None, "signup_mode": signup_mode, "execution_mode": execution_mode, "session_id": session.id, "identity_generated": bool(generated_identity)}))
        db.commit()
        return get_session(db, organization_id, session.id)


def start_newsletter_confirmation_test(
    db: Session,
    organization_id: str,
    actor_id: str,
    newsletter_id: str,
    account_id: str,
    profile_id: str | None,
    subscription_id: str,
    job_id: str | None = None,
    task_id: str | None = None,
) -> BrowserSession:
    """Confirm one submitted subscription through the account's saved Gmail profile."""
    with _account_start_lock(account_id):
        newsletter = db.scalar(select(Newsletter).where(Newsletter.organization_id == organization_id, Newsletter.id == newsletter_id))
        if not newsletter:
            raise ImportValidationError("Newsletter not found")
        if newsletter.status == "DISABLED":
            raise ImportValidationError("Disabled newsletters cannot be confirmed")
        if not newsletter.allowed_confirmation_domains:
            raise ImportValidationError("Configure at least one allowed confirmation domain before confirming")
        profile = None
        if profile_id:
            profile = db.scalar(select(NewsletterAutomationProfile).where(NewsletterAutomationProfile.organization_id == organization_id, NewsletterAutomationProfile.id == profile_id, NewsletterAutomationProfile.newsletter_id == newsletter_id))
            if not profile or profile.status not in {"CANDIDATE", "TESTING", "TEST_FAILED", "TEST_PASSED", "ACTIVE"}:
                raise ImportValidationError("Choose a candidate, tested, or active automation profile")
        account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id))
        if not account or account.lifecycle_status == "ARCHIVED":
            raise ImportValidationError("Account not found")
        subscription = db.scalar(select(NewsletterSubscription).where(NewsletterSubscription.organization_id == organization_id, NewsletterSubscription.id == subscription_id, NewsletterSubscription.account_id == account_id, NewsletterSubscription.newsletter_id == newsletter_id))
        if not subscription:
            raise ImportValidationError("Newsletter subscription not found for this account")
        if subscription.state == "CONFIRMED":
            raise ImportValidationError("This account is already confirmed for this newsletter")
        if subscription.state not in {"SUBMITTED", "WAITING_CONFIRMATION", "MANUAL_REVIEW", "FAILED"}:
            raise ImportValidationError("Run signup first so a confirmation message can be found")
        config = dict(profile.configuration_json or {}) if profile else {}
        config.update({
            "account_email": account.email,
            "newsletter_id": newsletter_id,
            "profile_id": profile.id if profile else "",
            "profile_version": str(profile.version) if profile else "",
            "signup_mode": "PROFILE" if profile else "ADAPTIVE_LEARNING",
            "subscription_id": subscription.id,
            "confirmation_subject_patterns": "\n".join(newsletter.subject_patterns or []),
            "confirmation_sender_patterns": "\n".join(newsletter.sender_patterns or []),
            "allowed_confirmation_domains": "\n".join(newsletter.allowed_confirmation_domains or []),
        })
        config["test_identity"] = generate_newsletter_account_identity(
            db, organization_id, actor_id, account_id
        )["values"]
        publisher_password = _publisher_signup_password(subscription)
        if publisher_password:
            config["signup_password"] = publisher_password
        if job_id:
            config["job_id"] = job_id
        if task_id:
            config["task_id"] = task_id
        subscription.state = "WAITING_CONFIRMATION"
        subscription.last_error = None
        subscription.last_checked_at = utcnow()
        session = _start_login_session_locked(db, organization_id, actor_id, account_id, purpose="newsletter_confirm", newsletter_config=config)
        db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="newsletter.confirmation.started", target_type="newsletter_subscription", target_id=subscription.id, result="success", metadata_json={"newsletter_id": newsletter_id, "account_id": account_id, "profile_version": profile.version if profile else None, "session_id": session.id}))
        db.commit()
        return get_session(db, organization_id, session.id)


def _start_login_session_locked(db: Session, organization_id: str, actor_id: str, account_id: str, purpose: str = "login", contacts_csv_path: str | None = None, mail_workflow: dict[str, object] | None = None, alerts_topics: list[str] | None = None, newsletter_config: dict[str, object] | None = None, account_preferences: dict[str, object] | None = None) -> BrowserSession:
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.id == account_id).with_for_update())
    if not account:
        raise ImportValidationError("Account not found")
    if account.lifecycle_status == "ARCHIVED":
        raise ImportValidationError("Archived accounts cannot open login sessions")
    profile = _ensure_browser_profile(db, account)
    if purpose not in {"login", "reuse", "contacts", "labels_markers", "security", "addons", "mail", "alerts", "credential_change", "newsletter", "newsletter_confirm", "account_preferences"}:
        raise ImportValidationError("Session purpose is not supported")
    external_newsletter = purpose == "newsletter" and account.account_type == "EXTERNAL_RECIPIENT"
    if purpose in {"reuse", "contacts", "labels_markers", "security", "addons", "mail", "alerts", "credential_change", "newsletter", "newsletter_confirm", "account_preferences"} and profile.authenticated_at is None and not external_newsletter:
        raise ImportValidationError("No authenticated profile saved for this account — complete a login session first so the profile gets saved")
    if purpose == "contacts" and not contacts_csv_path:
        raise ImportValidationError("A contacts CSV file is required for a contacts session")
    if profile.active_session_id:
        active = db.scalar(select(BrowserSession).where(BrowserSession.id == profile.active_session_id, BrowserSession.state.in_(ACTIVE_SESSION_STATES)))
        if active:
            browser_opened = bool(active.metadata_json.get("browser_opened"))
            browser_alive = browser_opened and local_browser_is_alive(profile.id, active.id)
            # The observer starts asynchronously. During that short hand-off
            # window Chrome is not visible in /proc yet, so treating the row as
            # stale would immediately terminate a perfectly valid new session.
            # A just-created row is an active reservation, not a stale browser.
            active_created_at = active.created_at
            # SQLite returns timezone-aware columns as naive datetimes even
            # though the model declares UTC-aware timestamps.
            if active_created_at.tzinfo is None:
                active_created_at = active_created_at.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (utcnow() - active_created_at).total_seconds())
            startup_grace_seconds = max(15.0, get_settings().browser_observe_interval * 5)
            observer_enabled = get_settings().browser_observer_enabled
            if browser_alive or not get_settings().browser_launch_enabled or (observer_enabled and age_seconds < startup_grace_seconds):
                raise AccountSessionConflictError("This account already has an open login session")
            _recover_stale_session(db, organization_id, actor_id, active)
    observer_mail_workflow = mail_workflow
    if purpose == "mail" and (mail_workflow or {}).get("action") == "confirmation_review":
        observer_mail_workflow = dict(mail_workflow or {})
        # Approved identity stays only in the observer payload. Session
        # metadata retains the public workflow settings, not decrypted values.
        observer_mail_workflow["_confirmation_identity"] = generate_newsletter_account_identity(
            db, organization_id, actor_id, account.id
        )["values"]
        observer_mail_workflow["_confirmation_email"] = account.email
    session_id = _new_id("session")
    launched, process_id = launch_local_browser(profile.id, session_id)
    logger.info("session %s: browser launch result=%s process_id=%s (browser_launch_enabled=%s, observer_enabled=%s)", session_id, launched, process_id, get_settings().browser_launch_enabled, get_settings().browser_observer_enabled)
    if not launched:
        # Nothing is persisted for this attempt, so the account/profile stay
        # clean and a later attempt can retry without a zombie session blocking it.
        raise BrowserLaunchError("Browser could not be launched (check BROWSER_LAUNCH_ENABLED and BROWSER_EXECUTABLE)")
    existing_auth = purpose in {"reuse", "contacts", "labels_markers", "security", "addons", "mail", "alerts", "credential_change", "newsletter", "newsletter_confirm", "account_preferences"} or external_newsletter
    addon_candidates = select_addon_candidates(3) if purpose == "addons" else []
    if purpose == "alerts" and not alerts_topics:
        raise ImportValidationError("Choose at least one Google Alerts topic")
    session_metadata = {"boundary": "human_login", "purpose": purpose, "browser_opened": True, "process_id": process_id, "contacts_csv_path": contacts_csv_path}
    if purpose == "mail":
        session_metadata["mail_workflow"] = dict(mail_workflow or {})
    if purpose == "addons":
        session_metadata.update({"addons_requested": 3, "addons_candidates": addon_candidates})
    if purpose == "alerts":
        session_metadata.update({"alerts_requested": len(alerts_topics or []), "alerts_topics": list(alerts_topics or [])})
    if purpose in {"newsletter", "newsletter_confirm"}:
        session_metadata.update({"newsletter_id": (newsletter_config or {}).get("newsletter_id"), "newsletter_profile_id": (newsletter_config or {}).get("profile_id"), "newsletter_profile_version": (newsletter_config or {}).get("profile_version"), "newsletter_signup_mode": (newsletter_config or {}).get("signup_mode"), "execution_mode": (newsletter_config or {}).get("execution_mode"), "learned_skill_id": (newsletter_config or {}).get("learned_skill_id"), "newsletter_subscription_id": (newsletter_config or {}).get("subscription_id"), "newsletter_auto_confirm": bool((newsletter_config or {}).get("newsletter_auto_confirm")), "newsletter_confirmation_required": bool((newsletter_config or {}).get("newsletter_confirmation_required")), "newsletter_external_recipient": bool((newsletter_config or {}).get("external_recipient")), "ephemeral_browser": bool((newsletter_config or {}).get("ephemeral_browser")), "newsletter_result": "IN_PROGRESS"})
        if (newsletter_config or {}).get("job_id"):
            session_metadata["newsletter_job_id"] = (newsletter_config or {}).get("job_id")
        if (newsletter_config or {}).get("task_id"):
            session_metadata["newsletter_task_id"] = (newsletter_config or {}).get("task_id")
        if (newsletter_config or {}).get("reuse_session_for_job"):
            session_metadata["newsletter_session_reuse_enabled"] = True
        if purpose == "newsletter":
            newsletter_config = dict(newsletter_config or {})
            newsletter_config["agent_settings"] = _newsletter_agent_runtime_settings(db, organization_id)
    if purpose == "account_preferences":
        session_metadata["account_preferences"] = dict(account_preferences or {})
    headless_newsletter = bool((newsletter_config or {}).get("headless_browser")) if purpose == "newsletter" else False
    runtime_type = "LOCAL_CHROME_HEADLESS_EPHEMERAL" if external_newsletter and headless_newsletter else "LOCAL_CHROME_EPHEMERAL" if external_newsletter else "LOCAL_CHROME"
    session_metadata["headless_browser"] = headless_newsletter
    session_metadata["browser_mode"] = "HEADLESS" if headless_newsletter else "VISIBLE"
    session = BrowserSession(id=session_id, organization_id=organization_id, account_id=account.id, profile_id=profile.id, state="RUNNING" if existing_auth else "LOGIN_REQUIRED", login_state="AUTHENTICATED" if existing_auth else "REQUIRED", runtime_type=runtime_type, metadata_json=session_metadata)
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
    observer_started = _start_observer(session.id, profile.id, organization_id, actor_id, purpose=purpose, contacts_csv_path=contacts_csv_path, addon_candidates=addon_candidates, mail_workflow=observer_mail_workflow, alerts_topics=alerts_topics, newsletter_config=newsletter_config, account_preferences=account_preferences)
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


def start_account_preferences_operation(db: Session, organization_id: str, actor_id: str, account_id: str, *, change_avatar: bool = True, change_theme: bool = True, random_theme: bool = True) -> BrowserSession:
    """Apply account preferences through Google's own visible pickers."""
    if not change_avatar and not change_theme:
        raise ImportValidationError("Enable at least one account preference")
    preferences = get_account_preferences(db, organization_id, account_id)
    return start_login_session(
        db,
        organization_id,
        actor_id,
        account_id,
        purpose="account_preferences",
        account_preferences={
            "gmail_theme": preferences.get("gmail_theme") or "default",
            "change_avatar": change_avatar,
            "change_theme": change_theme,
            "random_theme": random_theme,
            "profile_id": account_id,
        },
    )


def start_alerts_operation(db: Session, organization_id: str, actor_id: str, account_id: str, topic_count: int, topics: list[str] | None = None) -> BrowserSession:
    """Start an alerts operation with a validated per-account topic set."""
    try:
        topics = choose_alert_topics(db, organization_id, topic_count, requested=topics)
    except ValueError as exc:
        raise ImportValidationError(str(exc)) from exc
    session = start_login_session(db, organization_id, actor_id, account_id, purpose="alerts", alerts_topics=topics)
    session.metadata_json = {**(session.metadata_json or {}), "alerts_topics": topics, "alerts_requested": len(topics)}
    db.add(AuditEvent(id=_new_id("audit"), organization_id=organization_id, actor_id=actor_id, action="alerts.started", target_type="browser_session", target_id=session.id, result="success", metadata_json={"account_id": account_id, "topic_count": len(topics)}))
    db.commit()
    return get_session(db, organization_id, session.id)


def close_active_sessions(db: Session, organization_id: str, actor_id: str, group_ids: set[str] | None = None) -> dict:
    """Stop every active browser process in the requested account boundary."""
    query = (
        select(BrowserSession.id)
        .join(GmailAccount, GmailAccount.id == BrowserSession.account_id)
        .where(
            BrowserSession.organization_id == organization_id,
            GmailAccount.lifecycle_status != "ARCHIVED",
            BrowserSession.state.in_(ACTIVE_SESSION_STATES),
        )
    )
    if group_ids is not None:
        query = query.where(GmailAccount.group_id.in_(group_ids))
    session_ids = db.scalars(query).all()
    closed_ids: list[str] = []
    for session_id in session_ids:
        try:
            close_session(db, organization_id, actor_id, session_id)
            closed_ids.append(session_id)
        except ImportValidationError:
            continue
    return {"closed_count": len(closed_ids), "session_ids": closed_ids}


def close_group_sessions(db: Session, organization_id: str, actor_id: str, group_id: str) -> dict:
    """Stop every active browser process belonging to a group."""
    group = db.scalar(select(AccountGroup).where(AccountGroup.organization_id == organization_id, AccountGroup.id == group_id))
    if not group:
        raise ImportValidationError("Group not found")
    return {"group_id": group_id, **close_active_sessions(db, organization_id, actor_id, {group_id})}


def start_mail_workflow(db: Session, organization_id: str, actor_id: str, account_id: str, workflow: dict[str, object]) -> BrowserSession:
    """Start a subject-scoped Gmail action in the account's saved profile."""
    prepared = dict(workflow)
    if prepared.get("action") == "reply" and prepared.get("reply_mode") == "random":
        prepared["reply_body"] = secrets.choice(MAIL_REPLY_ROTATION_POOL)
    return start_login_session(db, organization_id, actor_id, account_id, purpose="mail", mail_workflow=prepared)


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
        session.failure_reason = None
    elif event_type == "VERIFICATION_REQUIRED":
        session.state = "NEEDS_ATTENTION"
        session.login_state = "VERIFICATION_REQUIRED"
        session.account.login_status = "VERIFICATION_REQUIRED"
        safe_verification_reasons = {
            "Complete the CAPTCHA in the open browser",
            "Complete phone verification in the open browser",
            "Complete the recovery-email check in the open browser",
            "Complete Google's security check in the open browser",
        }
        session.failure_reason = detail if detail in safe_verification_reasons else "Google requested additional verification"
    elif event_type == "UNKNOWN_PAGE":
        session.state = "NEEDS_ATTENTION"
        session.login_state = "UNKNOWN_PAGE"
        session.failure_reason = detail if detail == "Google page stayed blank; check the assigned proxy or network and retry" else "The worker encountered an unrecognized page"
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
    if event_type in {"LOGIN_COMPLETED", "VERIFICATION_REQUIRED", "ACCOUNT_NOT_FOUND"}:
        publish(db, organization_id, "account_status", f"Account status · {session.account.email}", f"{session.account.email}: {event_type.replace('_', ' ').title()}\nGroup: {session.account.group.name if session.account.group else 'Unassigned'}")
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
    if signal in {PageSignal.CAPTCHA, PageSignal.PHONE_VERIFICATION, PageSignal.MFA_OR_SECURITY_CHECK, PageSignal.RECOVERY_VERIFICATION}:
        if signal == PageSignal.MFA_OR_SECURITY_CHECK and is_totp_challenge_url(url) and _account_has_totp_secret(db, session):
            logger.info("session %s: MFA step on %s; auto sign-in will enter the TOTP", session_id, url)
        else:
            detail = "Complete the CAPTCHA in the open browser" if signal == PageSignal.CAPTCHA else "Complete phone verification in the open browser" if signal == PageSignal.PHONE_VERIFICATION else "Complete the recovery-email check in the open browser" if signal == PageSignal.RECOVERY_VERIFICATION else "Complete Google's security check in the open browser"
            if session.state == "NEEDS_ATTENTION" and session.login_state == "VERIFICATION_REQUIRED" and session.failure_reason == detail:
                return signal.value, action.value, session
            logger.warning("session %s: challenge detected on %s -> NEEDS_ATTENTION", session_id, url)
            updated = record_session_event(db, organization_id, actor_id, session_id, "VERIFICATION_REQUIRED", detail=detail)
            return signal.value, action.value, updated
    if signal == "UNKNOWN":
        logger.warning("session %s: unknown page %s -> NEEDS_ATTENTION", session_id, url)
        parsed_host = (parsed.hostname or "").lower().rstrip(".")
        blank_google_page = parsed_host in {"accounts.google.com", "mail.google.com", "www.google.com"} and not text.strip()
        detail = "Google page stayed blank; check the assigned proxy or network and retry" if blank_google_page else None
        updated = record_session_event(db, organization_id, actor_id, session_id, "UNKNOWN_PAGE", detail=detail)
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
    session_ids = [item.session_id for item in job.items if item.session_id]
    sessions = {
        session.id: session for session in db.scalars(
            select(BrowserSession).where(BrowserSession.id.in_(session_ids))
        ).all()
    } if job.operation_type == "mail" and session_ids else {}
    for item in job.items:
        states[item.state] = states.get(item.state, 0) + 1
        metadata = (sessions[item.session_id].metadata_json or {}) if item.session_id in sessions else {}
        items.append({"id": item.id, "account_id": item.account_id, "account_email": item.account.email, "session_id": item.session_id, "state": item.state, "attempts": item.attempts, "last_error": item.last_error, "paused_reason": item.paused_reason, "updated_at": item.updated_at, "completed_at": item.completed_at, "confirmed_messages": metadata.get("confirmed_messages"), "reviewed_message_count": metadata.get("reviewed_message_count"), "messages_needing_review": metadata.get("messages_needing_review")})
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
    reconcile_queue_mirrors(db, organization_id)
    return recovered


def reconcile_queue_mirrors(db: Session, organization_id: str) -> dict[str, int]:
    """Drop Redis dispatch mirrors that no longer match the durable state.

    Redis running sets are additive: they grow on dispatch and shrink on lease
    release. A crash, a Redis restart, or a job deleted out from under the
    worker can leave an account in the set forever. ``running_count`` is what
    enforces the concurrency limit, so a leaked member silently blocks every
    later wave while the job looks healthy. The database is authoritative, so
    the mirror is rebuilt from it rather than trusted.
    """
    queue = _queue()
    reconciled = {"running_members": 0, "bulk_members": 0, "active_profiles": 0, "restored_members": 0}

    # Login jobs: an account belongs in the running set only while one of its
    # items is actually RUNNING.
    for job in db.scalars(select(LoginJob).where(LoginJob.organization_id == organization_id)).all():
        live_accounts = {
            item.account_id
            for item in db.scalars(
                select(LoginJobItem).where(LoginJobItem.job_id == job.id, LoginJobItem.state == "RUNNING")
            ).all()
        }
        stale = queue.running_members(job.id) - live_accounts
        for account_id in stale:
            queue.remove_running(job.id, account_id)
            reconciled["running_members"] += 1
        if job.status == "RUNNING":
            for account_id in live_accounts - queue.running_members(job.id):
                queue.add_running(job.id, account_id)
                reconciled["restored_members"] += 1
        if job.status in {"COMPLETED", "CANCELLED", "FAILED"} and queue.running_count(job.id):
            queue.clear_running(job.id)

    # Newsletter tasks: same rule, plus the shared bulk-run scope counters.
    NewsletterJobModel, NewsletterTaskModel = _newsletter_models()
    scope_processing: dict[str, set[str]] = {}
    for job in db.scalars(select(NewsletterJobModel).where(NewsletterJobModel.organization_id == organization_id)).all():
        processing = db.scalars(
            select(NewsletterTaskModel).where(NewsletterTaskModel.job_id == job.id, NewsletterTaskModel.state == "PROCESSING")
        ).all()
        live_accounts = {task.account_id for task in processing}
        stale = queue.running_members(job.id) - live_accounts
        for account_id in stale:
            queue.remove_running(job.id, account_id)
            reconciled["running_members"] += 1
        if job.status == "RUNNING":
            for account_id in live_accounts - queue.running_members(job.id):
                queue.add_running(job.id, account_id)
                reconciled["restored_members"] += 1
        scope = str((job.retry_policy_json or {}).get("bulk_run_id") or job.id)
        scope_processing.setdefault(scope, set()).update(task.id for task in processing)
        if job.status in {"COMPLETED", "CANCELLED", "FAILED"}:
            queue.clear(job.id)
            queue.clear_running(job.id)
    for scope, task_ids in scope_processing.items():
        current = queue.bulk_running_members(scope)
        for task_id in current - task_ids:
            queue.remove_newsletter_bulk_running(scope, task_id)
            reconciled["bulk_members"] += 1
        for task_id in task_ids - current:
            queue.add_newsletter_bulk_running(scope, task_id)
            reconciled["restored_members"] += 1

    # Global active-profile cap: same reconciliation from live sessions.
    live_managed = {
        account_id
        for (account_id,) in db.execute(
            select(BrowserSession.account_id).where(
                BrowserSession.state.in_(ACTIVE_SESSION_STATES),
            )
        ).all()
    }
    active_members = queue.active_profile_members()
    for account_id in active_members - live_managed:
        queue.remove_active_profile(account_id)
        reconciled["active_profiles"] += 1
    for account_id in live_managed - active_members:
        queue.add_active_profile(account_id)
        reconciled["restored_members"] += 1
    if any(reconciled.values()):
        logger.warning("reconciled leaked dispatch mirrors: %s", reconciled)
    return reconciled
