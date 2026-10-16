import uuid
import secrets
import logging
import shutil
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import threading
import time

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .backup import BackupError, MAX_UPLOAD_BYTES, backup_archive_path, create_backup, list_backup_archives, restore_backup, validate_backup_file
from .profile_import import ProfileImportError, import_profiles, MAX_ARCHIVE_BYTES as MAX_PROFILE_ARCHIVE_BYTES
from . import backup_progress
from .db import Base, SessionLocal, engine, get_db, verify_production_schema
from .models import AccountGroup, AuditEvent, BrowserProfile, BrowserSession, Employee, EmployeeAuth, EmployeeSession, GmailAccount, JobLock, LoginJob, LoginJobItem, Newsletter, NewsletterAccountIdentity, NewsletterAutomationProfile, NewsletterGroup, NewsletterJob, NewsletterSubscription, Organization, OvhProxyGroupLink, ProxyInventory
from .schemas import AccountDeleteRequest, AccountListResponse, AccountMetadataUpdate, AccountTagsUpdate, AccountPreferencesApplyRequest, AccountPreferencesResponse, AccountPreferencesUpdate, AccountResponse, AccountStructuredInput, AlertTopicResponse, AlertsOperationRequest, AuthStatusResponse, AuditEventResponse, BrowserProfileResponse, BrowserSessionResponse, BulkImportRequest, BulkPreviewRequest, BulkPreviewResponse, ContactFileResponse, ContactsImportRequest, ContactsImportResponse, CredentialChangePlanConfirm, CredentialChangePlanCreate, CredentialChangePlanResponse, CredentialPlaintextResponse, CredentialRecordListResponse, CredentialRevisionResponse, CredentialRotateResponse, CredentialUpdate, EmployeeAuthResponse, EmployeeCreate, EmployeeGroupAccessResponse, EmployeeGroupAccessUpdate, EmployeeIdentityResponse, EmployeeLoginRequest, EmployeePasswordSet, EmployeePermissionsResponse, EmployeeResponse, EmployeeUpdate, GroupCreate, GroupMoveRequest, GroupResponse, GroupUpdate, ImportSummary, LoginJobCreate, LoginJobProgressResponse, LoginJobResponse, MailWorkflowRequest, NewsletterAccountIdentityResponse, NewsletterAccountIdentityUpdate, NewsletterAccountTargetsRequest, NewsletterBulkDiscoveryResponse, NewsletterCatalogImportResponse, NewsletterConfirmationTestRequest, NewsletterConfirmationTestResponse, NewsletterCreate, NewsletterDirectorySeedResponse, NewsletterDiscoveryResponse, NewsletterGroupCreate, NewsletterGroupResponse, NewsletterGroupUpdate, NewsletterHealthResponse, NewsletterIdentityGenerateRequest, NewsletterIdentityGenerateResponse, NewsletterJobBulkDelete, NewsletterJobCreate, NewsletterJobListResponse, NewsletterJobResponse, NewsletterJobTaskSelection, NewsletterProfileCreate, NewsletterProfileDetailResponse, NewsletterProfileResponse, NewsletterProfileUpdate, NewsletterRediscoveryResponse, NewsletterResponse, NewsletterSkillResponse, NewsletterSignupTestRequest, NewsletterSignupTestResponse, NewsletterSubscriptionListResponse, NewsletterSubscriptionResponse, NewsletterUpdate, NotificationTestRequest, OperationDraftCreate, OperationDraftResponse, PageObservationCreate, PageObservationResponse, PasswordResetConfirm, PasswordResetRequest, ProxyAssignmentRequest, ProxyBulkImportRequest, ProxyBulkImportResponse, ProxyDeleteRequest, ProxyGroupAssignmentRequest, ProxyGroupDistributionRequest, ProxyResponse, ProxyTestRequest, ProxyTestResponse, ProxyUpdate, SessionEventCreate, SessionStartRequest, WorkspaceSettingsResponse, WorkspaceSettingsUpdate
from .schemas import MailWorkflowBulkRequest, OvhPowerRequest, OvhProxyGroupLinkRequest
from .schemas import ProfileCompactRequest, ProfileExportRequest, ProfileSizesRequest
from .schemas import ProxyGroupCreateRequest, ProxyGroupUpdateRequest, GoDaddyAccountCreate, GoDaddyAccountUpdate, GoDaddyDnsGroupReplace, GoDaddyDnsRecord
from .godaddy_service import GoDaddyError, create_client as godaddy_client, list_accounts as list_godaddy_accounts, remove_account as remove_godaddy_account, save_account as save_godaddy_account
from .ovh_service import OvhError, configured_client, record_power_action
from .ovh_proxy_recovery import RecoveryError, create_recovery_job, fail_recovery_job, get_recovery_job, latest_recovery_job, recovery_configured, recovery_target, request_recovery_cancel, resume_recovery_jobs, start_recovery_thread, validate_openstack_config
from .auth import CurrentUser, SESSION_COOKIE, authenticate_request, create_employee_session, employee_auth_response, require_admin, require_user, set_employee_password, verify_password
from .operator_desktop import ensure_operator_desktop
from .newsletter_service import (
    NewsletterValidationError,
    create_newsletter,
    create_newsletter_group,
    create_newsletter_job,
    dispatch_newsletter_job,
    delete_newsletter_job_history,
    delete_newsletter_job_histories,
    create_newsletter_profile,
    disable_newsletter,
    disable_newsletter_group,
    enable_newsletter,
    enable_newsletter_group,
    get_newsletter,
    get_newsletter_group,
    get_newsletter_job,
    get_newsletter_profile_by_id,
    get_newsletter_subscription,
    list_newsletter_groups,
    list_newsletter_jobs,
    materialize_newsletter_job,
    list_newsletter_profiles,
    list_newsletter_skills,
    list_newsletter_subscriptions,
    list_newsletters,
    newsletter_automation_health_response,
    newsletter_skill_response,
    promote_newsletter_profile_by_id,
    reject_newsletter_profile_by_id,
    record_newsletter_discovery,
    import_newsletter_catalog,
    seed_newsletter_directory,
    rollback_newsletter_profile_by_id,
    update_newsletter,
    update_newsletter_group,
    update_newsletter_profile_by_id,
    retry_newsletter_job,
    skip_newsletter_tasks,
    start_newsletter_job,
    update_newsletter_job_state,
)
from .newsletter_discovery import NewsletterDiscoveryError, discover_newsletter
from .service import _UNSET, ImportValidationError, account_response, add_account, add_contact_file, archive_account, assign_proxy_accounts, cancel_credential_change_plan, cleanup_unsaved_invalid_profiles, close_active_sessions, close_group_sessions, close_session, confirm_credential_change_plan, contact_file_response, create_credential_change_plan, create_employee, create_login_job, create_operation_draft, credential_records, credential_revisions, credential_plaintext, delete_account_group, delete_contact_file, dispatch_login_job, distribute_proxies_to_group, generate_newsletter_account_identity, get_account, get_account_preferences, get_employee_group_access, get_login_job, get_newsletter_account_identity, get_session, get_workspace_settings, import_accounts, import_proxy_inventory, list_accounts, list_browser_profiles, list_contact_files, list_credential_change_plans, list_employees, list_login_jobs, list_operation_drafts, list_proxies, list_sessions, local_browser_is_alive, login_job_progress, move_account_group, observe_session_page, preview_import, record_session_event, recover_orphaned_sessions, recover_stale_locks, remove_proxy_assignments, replace_proxy_group_assignments, resume_login_job, rotate_account_credentials, session_response, set_employee_group_access, start_account_preferences_operation, start_addons_operation, start_alerts_operation, start_contacts_import, start_credential_change_plan, start_labels_markers_operation, start_login_session, start_mail_workflow, start_newsletter_confirmation_test, start_newsletter_signup_test, start_security_operation, test_proxy, update_account_metadata, update_account_tags, update_account_preferences, update_employee, update_credentials, update_group, update_login_job_state, update_newsletter_account_identity, update_proxy, update_workspace_settings, upload_account_preference_avatar
from .service import create_mail_workflow_job
from .service import compact_account_profile, reconcile_imported_profiles
from .service import create_proxy_group, list_proxy_groups, update_proxy_group
from .notifications import NotificationError, consume_password_reset, create_password_reset, send_group_status_report, send_test, update_notification_settings, publish
from .alerts import list_alert_topics, seed_default_alert_topics
from .worker.worker import start_login_worker, stop_login_worker
from .runtime import focus_local_browser_profile
from .runtime import local_browser_profiles_alive
from .profile_export import profile_path, profile_size_bytes, write_profiles_zip

settings = get_settings()
_BACKUP_OPERATION_LOCK = threading.Lock()

_AUTH_LIMIT_LOCK = threading.Lock()
_AUTH_LIMIT_BUCKETS: dict[tuple[str, str], list[float]] = {}


def _enforce_auth_rate_limit(request: Request, bucket: str) -> None:
    """Per-process fallback limiter; production Nginx also limits these paths."""
    now = time.monotonic()
    client = request.client.host if request.client else "unknown"
    key = (bucket, client)
    window, limit = (600.0, 5) if bucket == "password-reset" else (600.0, 20)
    with _AUTH_LIMIT_LOCK:
        attempts = [stamp for stamp in _AUTH_LIMIT_BUCKETS.get(key, []) if now - stamp < window]
        if len(attempts) >= limit:
            _AUTH_LIMIT_BUCKETS[key] = attempts
            raise HTTPException(status_code=429, detail="Too many requests. Try again later.", headers={"Retry-After": str(int(window - (now - attempts[0])))})
        attempts.append(now)
        _AUTH_LIMIT_BUCKETS[key] = attempts
        if len(_AUTH_LIMIT_BUCKETS) > 5000:
            for stale_key in [k for k, values in _AUTH_LIMIT_BUCKETS.items() if not values or now - values[-1] >= window]:
                _AUTH_LIMIT_BUCKETS.pop(stale_key, None)


async def _read_upload_bounded(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        while chunk := await file.read(min(64 * 1024, max_bytes + 1 - total)):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"Uploaded file exceeds the {max_bytes // (1024 * 1024)} MB limit")
            chunks.append(chunk)
    finally:
        await file.close()
    return b"".join(chunks)

logger = logging.getLogger("gmail_ops")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)


def startup() -> None:
    if settings.environment.lower() == "production":
        verify_production_schema()
    else:
        Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        org = db.get(Organization, settings.demo_org_id)
        if not org:
            org = Organization(id=settings.demo_org_id, name="Northstar Operations")
            db.add(org)
            for key, name, description in [("sales-us", "Sales · US", "North America sales account pool"), ("support", "Customer Support", "Customer care account pool"), ("partnerships", "Partnerships", "Partner operations account pool")]:
                db.add(AccountGroup(id=f"group_{key}", organization_id=org.id, name=name, description=description, capacity=300))
            db.commit()
        seed_default_alert_topics(db, settings.demo_org_id)
        reconcile_imported_profiles(db, settings.demo_org_id)
        cleanup_unsaved_invalid_profiles(db, settings.demo_org_id)
        recover_orphaned_sessions(db, settings.demo_org_id)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    startup()
    logger.info(
        "startup complete: environment=%s worker=%s browser_launch=%s observer=%s observer_interval=%.1fs redis=%s",
        settings.environment,
        settings.worker_enabled,
        settings.browser_launch_enabled,
        settings.browser_observer_enabled,
        settings.browser_observe_interval,
        settings.redis_url,
    )
    if settings.worker_enabled:
        start_login_worker()
    resume_recovery_jobs()
    yield
    stop_login_worker()


app = FastAPI(title=settings.app_name, version="0.1.0", description="Profile-backed Gmail account operations; no Gmail API or OAuth integration", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def pause_mutations_during_backup(request: Request, call_next):
    if (
        _BACKUP_OPERATION_LOCK.locked()
        and request.url.path.startswith("/api/v1/")
        and not request.url.path.startswith("/api/v1/backups")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
    ):
        return JSONResponse(status_code=503, headers={"Retry-After": "30"}, content={"detail": "Workspace changes are temporarily paused while an encrypted backup or restore is running. Retry shortly."})
    return await call_next(request)


@app.middleware("http")
async def require_workspace_auth(request: Request, call_next):
    """Resolve the employee session before any workspace API handler runs."""
    if request.method != "OPTIONS" and request.url.path.startswith("/api/v1/") and request.url.path not in {"/api/v1/health", "/api/v1/auth/status", "/api/v1/auth/setup", "/api/v1/auth/login", "/api/v1/auth/password-reset/request", "/api/v1/auth/password-reset/confirm"}:
        auth_db = SessionLocal()
        try:
            user = authenticate_request(request, auth_db)
            if user is None:
                return JSONResponse(status_code=401, headers={"WWW-Authenticate": "Cookie"}, content={"detail": "Sign in to the workspace"})
            request.state.user = user
            path_parts = [part for part in request.url.path.split("/") if part]
            if not user.is_admin:
                account_id = path_parts[path_parts.index("accounts") + 1] if "accounts" in path_parts and len(path_parts) > path_parts.index("accounts") + 1 else None
                if account_id and account_id not in {"single", "bulk"}:
                    account = auth_db.get(GmailAccount, account_id)
                    minimum = "VIEWER" if request.method in {"GET", "HEAD"} else "OPERATOR"
                    if "credentials" in path_parts and "plaintext" in path_parts:
                        minimum = "MANAGER"
                    if not account or not user.can_access_group(account.group_id, minimum):
                        return JSONResponse(status_code=404, content={"detail": "Resource not found"})
            if "sessions" in path_parts and len(path_parts) > path_parts.index("sessions") + 1 and path_parts[path_parts.index("sessions") + 1] not in {"close-active", "history"}:
                session_id = path_parts[path_parts.index("sessions") + 1]
                session = auth_db.get(BrowserSession, session_id)
                account = auth_db.get(GmailAccount, session.account_id) if session else None
                minimum = "VIEWER" if request.method in {"GET", "HEAD"} else "OPERATOR"
                if not account or not user.can_access_group(account.group_id, minimum) or not session or session.owner_employee_id != user.employee_id:
                    return JSONResponse(status_code=404, content={"detail": "Resource not found"})
        finally:
            auth_db.close()
    return await call_next(request)


def _current_user(request: Request, db: Session):
    return require_user(request, db)


def _actor(request: Request, db: Session) -> str:
    return _current_user(request, db).employee_id


def _admin(request: Request, db: Session):
    return require_admin(_current_user(request, db))


def _require_backup_quiescence(db: Session) -> None:
    active_session = db.scalar(select(BrowserSession.id).where(BrowserSession.organization_id == settings.demo_org_id, BrowserSession.state.in_(["STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"]), BrowserSession.closed_at.is_(None)).limit(1))
    if active_session:
        raise HTTPException(status_code=409, detail="Close all active browser sessions before creating or restoring a backup.")
    active_login = db.scalar(select(LoginJobItem.id).where(LoginJobItem.organization_id == settings.demo_org_id, LoginJobItem.state == "RUNNING").limit(1))
    if active_login:
        raise HTTPException(status_code=409, detail="Wait for running account jobs to finish before creating or restoring a backup.")
    active_newsletter = db.scalar(select(NewsletterJob.id).where(NewsletterJob.organization_id == settings.demo_org_id, NewsletterJob.status == "RUNNING").limit(1))
    if active_newsletter:
        raise HTTPException(status_code=409, detail="Wait for running newsletter jobs to finish before creating or restoring a backup.")


def _account_operator(request: Request, db: Session):
    """Require an operator-level employee (or administrator)."""
    user = _current_user(request, db)
    if not user.is_admin and not any(user.can_access_group(group_id, "OPERATOR") for group_id in user.group_access):
        raise HTTPException(status_code=403, detail="Account operator access is required")
    return user


def _authorize_proxy_accounts(request: Request, db: Session, account_ids: list[str]) -> None:
    """Keep proxy mutations inside the caller's assigned account groups."""
    user = _account_operator(request, db)
    accounts = db.scalars(select(GmailAccount).where(
        GmailAccount.organization_id == settings.demo_org_id,
        GmailAccount.id.in_(set(account_ids)),
        GmailAccount.lifecycle_status != "ARCHIVED",
    )).all() if account_ids else []
    if len(accounts) != len(set(account_ids)):
        raise HTTPException(status_code=404, detail="One or more accounts were not found")
    if not user.is_admin and any(not user.can_access_group(account.group_id, "OPERATOR") for account in accounts):
        raise HTTPException(status_code=404, detail="Resource not found")


def _authorize_proxy_record(request: Request, db: Session, proxy_id: str):
    """Authorize mutations to a proxy already used by accounts."""
    user = _account_operator(request, db)
    if user.is_admin:
        return user
    account_groups = set(db.scalars(select(GmailAccount.group_id).where(
        GmailAccount.organization_id == settings.demo_org_id,
        GmailAccount.proxy_id == proxy_id,
        GmailAccount.lifecycle_status != "ARCHIVED",
    )).all())
    if any(not user.can_access_group(group_id, "OPERATOR") for group_id in account_groups):
        raise HTTPException(status_code=404, detail="Resource not found")
    return user


def _account_access(request: Request, db: Session, account_id: str, minimum: str = "VIEWER") -> GmailAccount:
    user = _current_user(request, db)
    account = get_account(db, settings.demo_org_id, account_id)
    if not account or (not user.is_admin and not user.can_access_group(account.group_id, minimum)):
        raise HTTPException(status_code=404, detail="Resource not found")
    return account


def _authorize_group(request: Request, group_id: str, minimum: str = "VIEWER") -> None:
    db = SessionLocal()
    try:
        group = db.get(AccountGroup, group_id)
        user = _current_user(request, db)
        if not group or group.organization_id != settings.demo_org_id or (not user.is_admin and not user.can_access_group(group_id, minimum)):
            raise HTTPException(status_code=404, detail="Resource not found")
    finally:
        db.close()


def _visible_groups(request: Request, db: Session) -> set[str] | None:
    user = _current_user(request, db)
    return None if user.is_admin else set(user.group_access)


def _nl_http_status(message: str) -> int:
    lowered = message.lower()
    if "not found" in lowered or "choose a valid account group" in lowered or "inaccessible" in lowered:
        return 404
    if "cannot" in lowered or "already" in lowered or "duplicate" in lowered:
        return 409
    return 422


@app.get("/api/v1/auth/status", response_model=AuthStatusResponse)
def auth_status(request: Request, db: Session = Depends(get_db)) -> dict:
    user = authenticate_request(request, db)
    setup_required = db.scalar(select(Employee.id).where(Employee.organization_id == settings.demo_org_id, Employee.auth.has())) is None
    return {"authenticated": user is not None, "setup_required": setup_required, "session": employee_auth_response(user) if user else None}


@app.post("/api/v1/auth/setup", response_model=EmployeeAuthResponse, status_code=status.HTTP_201_CREATED)
def initial_admin_setup(payload: EmployeeLoginRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    if not settings.allow_initial_admin_setup:
        raise HTTPException(status_code=404, detail="Initial administrator setup is disabled")
    if not settings.allow_initial_admin_setup:
        raise HTTPException(status_code=403, detail="Initial setup is disabled")
    if db.scalar(select(EmployeeAuth.id).where(EmployeeAuth.organization_id == settings.demo_org_id)):
        raise HTTPException(status_code=409, detail="Initial administrator is already configured")
    employee = db.scalar(select(Employee).where(Employee.organization_id == settings.demo_org_id, Employee.email == payload.email))
    if employee is None:
        employee = db.scalar(select(Employee).where(Employee.organization_id == settings.demo_org_id, Employee.role == "ADMINISTRATOR").order_by(Employee.created_at).limit(1))
    if employee is None:
        employee = Employee(id=f"employee_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, full_name="Workspace Administrator", email=payload.email, role="ADMINISTRATOR", status="ACTIVE")
        db.add(employee)
        db.flush()
    employee.role = "ADMINISTRATOR"
    employee.status = "ACTIVE"
    set_employee_password(db, employee, payload.password)
    token = create_employee_session(db, employee, payload.password)
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.environment.lower() == "production", samesite="lax", max_age=settings.session_ttl_hours * 3600, path="/")
    return employee_auth_response(CurrentUser(employee.id, employee.organization_id, employee.full_name, employee.email, employee.role, {}, "setup"))


@app.post("/api/v1/auth/login", response_model=EmployeeAuthResponse)
def employee_login(payload: EmployeeLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    _enforce_auth_rate_limit(request, "login")
    employee = db.scalar(select(Employee).where(Employee.organization_id == settings.demo_org_id, Employee.email == payload.email))
    auth = db.scalar(select(EmployeeAuth).where(EmployeeAuth.employee_id == employee.id)) if employee else None
    now = datetime.now(timezone.utc)
    locked_until = auth.locked_until if auth else None
    if locked_until and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if not employee or not auth or employee.status != "ACTIVE" or (locked_until and locked_until > now) or not verify_password(payload.password, auth.password_hash):
        if auth and not (locked_until and locked_until > now):
            auth.failed_attempts += 1
            if auth.failed_attempts >= 5:
                auth.locked_until = now + timedelta(minutes=15)
            db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    auth.failed_attempts = 0
    auth.locked_until = None
    token = create_employee_session(db, employee, payload.password)
    auth.last_login_at = now
    db.commit()
    employee = db.scalar(select(Employee).options(selectinload(Employee.group_access)).where(Employee.id == employee.id))
    user = CurrentUser(employee.id, employee.organization_id, employee.full_name, employee.email, employee.role, {a.group_id: a.access_level for a in employee.group_access})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.environment.lower() == "production", samesite="lax", max_age=settings.session_ttl_hours * 3600, path="/")
    publish(db, employee.organization_id, "employee_login", "Employee signed in", f"{employee.full_name} ({employee.email}) signed in to Northstar.")
    return employee_auth_response(user)


@app.post("/api/v1/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(payload: PasswordResetRequest, request: Request, db: Session = Depends(get_db)) -> None:
    _enforce_auth_rate_limit(request, "password-reset")
    employee = db.scalar(select(Employee).where(Employee.organization_id == settings.demo_org_id, Employee.email == payload.email.strip().lower()))
    if employee:
        create_password_reset(db, settings.demo_org_id, employee, request.headers.get("origin") or str(request.base_url).rstrip("/"))


@app.post("/api/v1/auth/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(payload: PasswordResetConfirm, db: Session = Depends(get_db)) -> None:
    if not consume_password_reset(db, payload.token, payload.password):
        raise HTTPException(status_code=400, detail="This reset link is invalid or expired")


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def employee_logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        import hashlib
        session = db.scalar(select(EmployeeSession).where(EmployeeSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        if session:
            session.revoked_at = datetime.now(timezone.utc)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@app.get("/api/v1/auth/me", response_model=EmployeeAuthResponse)
def current_employee(request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    return employee_auth_response(user)


@app.put("/api/v1/employees/{employee_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def set_employee_login_password(employee_id: str, payload: EmployeePasswordSet, request: Request, db: Session = Depends(get_db)) -> None:
    admin = _admin(request, db)
    employee = db.scalar(select(Employee).where(Employee.organization_id == settings.demo_org_id, Employee.id == employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    set_employee_password(db, employee, payload.password)
    if employee.status == "INVITED":
        employee.status = "ACTIVE"
    now = datetime.now(timezone.utc)
    for active_session in db.scalars(select(EmployeeSession).where(EmployeeSession.employee_id == employee.id, EmployeeSession.revoked_at.is_(None))).all():
        active_session.revoked_at = now
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, actor_id=admin.employee_id, action="employee.password.updated", target_type="employee", target_id=employee.id, result="success", metadata_json={"status": employee.status}))
    db.commit()


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "1", "boundary": "ready_for_login"}


@app.get("/api/v1/desktop/authorize", status_code=status.HTTP_204_NO_CONTENT)
def authorize_browser_desktop(request: Request, db: Session = Depends(get_db)) -> Response:
    """Nginx auth_request target for the embedded browser desktop.

    Viewing and control access follows the signed-in operator's group scope.
    """
    user = _account_operator(request, db)
    if not settings.browser_desktop_enabled:
        raise HTTPException(status_code=404, detail="The browser desktop is not enabled")
    # Nginx also authorizes every noVNC asset through this route. Loading a
    # script or image must only check access; the WebSocket request starts or
    # reuses the operator's desktop and receives its private port.
    if request.headers.get("x-original-uri", "").startswith("/desktop/"):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        desktop = ensure_operator_desktop(user.employee_id)
    except Exception as exc:  # noqa: BLE001 - no fallback to the shared desktop
        # The manager raises only fixed, non-sensitive diagnostics here. Keep
        # the reason in the service journal so a failed noVNC handshake is
        # distinguishable from a missing X/VNC process without exposing IDs.
        logger.error("private desktop could not be initialized (%s): %s", type(exc).__name__, str(exc))
        raise HTTPException(status_code=503, detail="Your private browser desktop could not be started") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"X-Desktop-Websocket-Port": str(desktop["websocket_port"])})


@app.get("/api/v1/desktop/status")
def browser_desktop_status(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    _account_operator(request, db)
    return {"enabled": settings.browser_desktop_enabled}


@app.get("/api/v1/desktop/runtime")
def browser_desktop_runtime(request: Request, db: Session = Depends(get_db)) -> dict[str, int]:
    """Expose only the runtime limits needed by the desktop launcher."""
    _account_operator(request, db)
    workspace = get_workspace_settings(db, settings.demo_org_id)
    return {
        "default_login_concurrency": int(workspace["default_login_concurrency"]),
        "max_active_profiles": int(workspace["max_active_profiles"]),
    }


@app.post("/api/v1/sessions/{session_id}/focus")
def focus_browser_session(session_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    user = _account_operator(request, db)
    session = get_session(db, settings.demo_org_id, session_id)
    if not session or session.state not in {"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}:
        raise HTTPException(status_code=409, detail="This browser session is no longer open")
    if not user.is_admin and not user.can_access_group(session.account.group_id, "OPERATOR"):
        raise HTTPException(status_code=404, detail="Resource not found")
    if session.owner_employee_id != user.employee_id:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not local_browser_is_alive(session.profile_id, session.id):
        raise HTTPException(status_code=409, detail="Chrome is not running for this account yet")
    # Authenticated operators use a private X display. Focus the Chrome window
    # on the display that owns this session instead of the shared install-time
    # display, otherwise the request may succeed against an empty desktop.
    owner_employee_id = session.owner_employee_id or user.employee_id
    try:
        desktop = ensure_operator_desktop(owner_employee_id)
    except (RuntimeError, OSError) as exc:
        logger.warning("browser session desktop could not be resolved for focus (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="The browser desktop for this session is unavailable. Retry the connection.") from exc
    if not focus_local_browser_profile(session.profile_id, display=str(desktop["display"])):
        raise HTTPException(status_code=503, detail="The account browser is running, but its window could not be focused on this operator's desktop. Refresh the session list and retry.")
    return {"focused": True}


@app.get("/api/v1/groups", response_model=list[GroupResponse])
def groups(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    visible = _visible_groups(request, db)
    rows = db.execute(select(AccountGroup, func.count(GmailAccount.id)).outerjoin(GmailAccount, (GmailAccount.group_id == AccountGroup.id) & (GmailAccount.lifecycle_status != "ARCHIVED") & (GmailAccount.account_type != "EXTERNAL_RECIPIENT")).where(AccountGroup.organization_id == settings.demo_org_id).group_by(AccountGroup.id).order_by(AccountGroup.name)).all()
    if visible is not None:
        rows = [row for row in rows if row[0].id in visible]
    return [{"id": group.id, "name": group.name, "description": group.description, "capacity": group.capacity, "account_count": count, "ready_count": count, "issue_count": 0, "status": group.status} for group, count in rows]


@app.post("/api/v1/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    group = AccountGroup(id=f"group_{payload.name.lower().replace(' ', '-')[:30]}_{uuid.uuid4().hex[:6]}", organization_id=settings.demo_org_id, name=payload.name, description=payload.description, capacity=payload.capacity)
    db.add(group)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A group with this name already exists") from exc
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, actor_id=admin.employee_id, action="group.created", target_type="group", target_id=group.id, result="success", metadata_json={"name": group.name}))
    db.commit()
    return {"id": group.id, "name": group.name, "description": group.description, "capacity": group.capacity, "account_count": 0, "ready_count": 0, "issue_count": 0, "status": group.status}


@app.patch("/api/v1/groups/{group_id}", response_model=GroupResponse)
def edit_group(group_id: str, payload: GroupUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return update_group(db, settings.demo_org_id, admin.employee_id, group_id, payload.name, payload.description, payload.capacity, payload.status)
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=404 if "not found" in message else 409, detail=str(exc)) from exc


@app.delete("/api/v1/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_group(group_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    admin = _admin(request, db)
    try:
        delete_account_group(db, settings.demo_org_id, admin.employee_id, group_id)
    except ImportValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=404 if str(exc) == "Group not found" else 409, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This group is still referenced by workspace data. Remove those references before deleting it.") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/employees", response_model=list[EmployeeResponse])
def employees(q: str | None = Query(default=None, max_length=320), employee_status: str | None = Query(default=None, alias="status"), request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    _admin(request, db)
    return list_employees(db, settings.demo_org_id, q, employee_status)


@app.post("/api/v1/employees", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
def add_employee(payload: EmployeeCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return create_employee(db, settings.demo_org_id, admin.employee_id, payload.full_name, payload.email, payload.role, payload.notes)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/v1/employees/{employee_id}", response_model=EmployeeResponse)
def edit_employee(employee_id: str, payload: EmployeeUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return update_employee(db, settings.demo_org_id, admin.employee_id, employee_id, payload.full_name, payload.email, payload.role, payload.status, payload.notes if "notes" in payload.model_fields_set else _UNSET)
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=404 if "not found" in message else 409, detail=str(exc)) from exc


@app.delete("/api/v1/employees/{employee_id}", response_model=EmployeeResponse)
def deactivate_employee(employee_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return update_employee(db, settings.demo_org_id, admin.employee_id, employee_id, None, None, None, "DEACTIVATED")
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/employees/{employee_id}/group-access", response_model=list[EmployeeGroupAccessResponse])
def employee_group_access(employee_id: str, request: Request, db: Session = Depends(get_db)) -> list[dict]:
    _admin(request, db)
    try:
        return get_employee_group_access(db, settings.demo_org_id, employee_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/v1/employees/{employee_id}/group-access", response_model=EmployeeResponse)
def save_employee_group_access(employee_id: str, payload: EmployeeGroupAccessUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return set_employee_group_access(db, settings.demo_org_id, admin.employee_id, employee_id, [item.model_dump() for item in payload.assignments])
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=404 if "not found" in message else 422, detail=str(exc)) from exc


@app.get("/api/v1/settings", response_model=WorkspaceSettingsResponse)
def workspace_settings(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    return get_workspace_settings(db, settings.demo_org_id)


@app.put("/api/v1/settings", response_model=WorkspaceSettingsResponse)
def save_workspace_settings(payload: WorkspaceSettingsUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    values = payload.model_dump(exclude_unset=True)
    notification_keys = {"telegram_enabled", "telegram_bot_token", "telegram_chat_id", "email_enabled", "email_provider", "email_recipient_email", "email_from_address", "resend_api_key", "smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_use_tls", "notify_employee_logins", "notify_group_reports", "notify_account_status", "notify_operation_failures"}
    notification_values = {key: values.pop(key) for key in list(values) if key in notification_keys}
    try:
        update_workspace_settings(db, settings.demo_org_id, admin.employee_id, values)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if notification_values:
        update_notification_settings(db, settings.demo_org_id, admin.employee_id, notification_values)
    return get_workspace_settings(db, settings.demo_org_id)


@app.get("/api/v1/backups/progress/{operation_id}")
def workspace_backup_progress(operation_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    progress = backup_progress.get(operation_id, admin.employee_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Backup progress is unavailable.")
    return progress


@app.get("/api/v1/backups/catalog")
def workspace_backup_catalog(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    return {"items": list_backup_archives()}


@app.get("/api/v1/backups/archive/{backup_name}")
def download_saved_workspace_backup(backup_name: str, request: Request, db: Session = Depends(get_db)) -> FileResponse:
    _admin(request, db)
    archive = backup_archive_path(backup_name)
    if archive is None:
        raise HTTPException(status_code=404, detail="Backup archive not found.")
    return FileResponse(archive, media_type="application/octet-stream", filename=archive.name, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/backups")
def download_workspace_backup(request: Request, passphrase: str = Form(...), operation_id: str | None = Form(None), include_profiles: bool = Form(True), include_runtime_config: bool = Form(True), db: Session = Depends(get_db)) -> FileResponse:
    admin = _admin(request, db)
    _require_backup_quiescence(db)
    if not _BACKUP_OPERATION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another backup or restore operation is already running.")
    if operation_id:
        backup_progress.begin(operation_id, admin.employee_id, "download")
    try:
        archive = create_backup(passphrase, persistent=True, kind="workspace", created_by=admin.employee_id, include_profiles=include_profiles, include_runtime_config=include_runtime_config, progress=lambda stage, done, total: backup_progress.update(operation_id, stage, done, total))
    except BackupError as exc:
        backup_progress.finish(operation_id, False)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        backup_progress.finish(operation_id, False)
        logger.error("Workspace backup generation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="The backup could not be generated. Check server storage and database health.") from exc
    finally:
        _BACKUP_OPERATION_LOCK.release()
    try:
        db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, actor_id=admin.employee_id, action="workspace.backup.created", target_type="workspace_backup", target_id=archive.name, result="success", metadata_json={"format_version": 1, "include_profiles": include_profiles, "include_runtime_config": include_runtime_config}))
        db.commit()
    except Exception:
        backup_progress.finish(operation_id, False)
        archive.unlink(missing_ok=True)
        archive.with_suffix(".json").unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The backup file was prepared, but its audit record could not be saved. Retry the backup.")
    backup_progress.finish(operation_id, True)
    return FileResponse(archive, media_type="application/octet-stream", filename=archive.name, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/backups/restore")
def restore_workspace_backup(request: Request, file: UploadFile = File(...), passphrase: str = Form(...), operation_id: str | None = Form(None), db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    _require_backup_quiescence(db)
    if not _BACKUP_OPERATION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another backup or restore operation is already running.")
    if operation_id:
        backup_progress.begin(operation_id, admin.employee_id, "restore")
    progress = lambda stage, done, total: backup_progress.update(operation_id, stage, done, total)
    temporary: Path | None = None
    uploaded: Path | None = None
    safety_archive: Path | None = None
    try:
        temporary = Path(tempfile.mkdtemp(prefix="northstar-upload-"))
        uploaded = temporary / "backup.nstar"
        total = 0
        with uploaded.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Backup exceeds the 20 GB upload limit.")
                target.write(chunk)
                progress("Receiving archive", total, file.size)
        if not total:
            raise HTTPException(status_code=422, detail="Choose a Northstar backup file to restore.")
        validate_backup_file(uploaded, passphrase, progress=progress)
        # Preserve the current state first, encrypted with the same passphrase.
        safety_archive = create_backup(passphrase, persistent=True, kind="safety", created_by=admin.employee_id, progress=lambda stage, done, total: progress(f"Safety copy · {stage}", done, total))
        result = restore_backup(uploaded, passphrase, db, actor_id=admin.employee_id, safety_backup_name=safety_archive.name, progress=progress)
        result["safety_backup"] = safety_archive.name
        backup_progress.finish(operation_id, True)
        return result
    except BackupError as exc:
        backup_progress.finish(operation_id, False)
        detail = str(exc)
        if safety_archive:
            detail += f" The pre-restore safety snapshot is {safety_archive.name}."
        raise HTTPException(status_code=422, detail=detail) from exc
    except HTTPException:
        backup_progress.finish(operation_id, False)
        raise
    except Exception as exc:
        backup_progress.finish(operation_id, False)
        logger.error("Workspace backup restore failed (%s); a safety snapshot may be available", type(exc).__name__)
        snapshot_note = f" Safety snapshot: {safety_archive.name}." if safety_archive else ""
        raise HTTPException(status_code=500, detail=f"Restore failed. The database transaction was rolled back where possible.{snapshot_note}") from exc
    finally:
        file.file.close()
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)
        _BACKUP_OPERATION_LOCK.release()


@app.get("/api/v1/backups/safety/{backup_name}")
def download_restore_safety_backup(backup_name: str, request: Request, db: Session = Depends(get_db)) -> FileResponse:
    _admin(request, db)
    if not backup_name.startswith("restore-safety-") or not backup_name.endswith(".nstar") or "/" in backup_name or "\\" in backup_name:
        raise HTTPException(status_code=404, detail="Safety backup not found.")
    archive = backup_archive_path(backup_name)
    if archive is None:
        raise HTTPException(status_code=404, detail="Safety backup not found.")
    return FileResponse(archive, media_type="application/octet-stream", filename=backup_name, headers={"Cache-Control": "no-store"})


def _receive_profile_archive(file: UploadFile, policy: str, db: Session, actor_id: str, account_id: str | None = None) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".zip", ".rar"}:
        raise HTTPException(status_code=422, detail="Choose a .zip or .rar profile archive.")
    if not _BACKUP_OPERATION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another backup or profile import is running. Retry when it finishes.")
    temporary: Path | None = None
    try:
        profile_parent = Path(settings.profile_root).resolve().parent
        profile_parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="northstar-profile-upload-", dir=profile_parent))
        uploaded = temporary / f"upload{suffix}"
        total = 0
        with uploaded.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_PROFILE_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail="Profile archive exceeds the 20 GB upload limit.")
                target.write(chunk)
        if not total:
            raise HTTPException(status_code=422, detail="Choose a nonempty profile archive.")
        return import_profiles(db, settings.demo_org_id, actor_id, uploaded, policy, account_id)
    except ProfileImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        file.file.close()
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)
        _BACKUP_OPERATION_LOCK.release()


@app.post("/api/v1/backups/profiles/import")
def import_workspace_profiles(request: Request, file: UploadFile = File(...), policy: str = Form(...), db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    return _receive_profile_archive(file, policy, db, admin.employee_id)


@app.post("/api/v1/backups/profiles/account/{account_id}")
def import_account_profile(account_id: str, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    actor = _account_operator(request, db)
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == settings.demo_org_id, GmailAccount.id == account_id, GmailAccount.lifecycle_status != "ARCHIVED"))
    if not account or not actor.can_access_group(account.group_id, "OPERATOR"):
        raise HTTPException(status_code=404, detail="Account not found.")
    return _receive_profile_archive(file, "replace", db, actor.employee_id, account_id)


@app.get("/api/v1/infrastructure/ovh")
def ovh_resources(request: Request, db: Session = Depends(get_db)) -> dict:
    _account_operator(request, db)
    try:
        with configured_client(db, settings.demo_org_id) as client:
            return client.list_resources()
    except OvhError as exc:
        logger.warning("OVH inventory request failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/infrastructure/ovh/proxy-groups")
def ovh_proxy_group_links(request: Request, db: Session = Depends(get_db)) -> dict:
    _account_operator(request, db)
    links = db.scalars(select(OvhProxyGroupLink).where(OvhProxyGroupLink.organization_id == settings.demo_org_id).order_by(OvhProxyGroupLink.proxy_group, OvhProxyGroupLink.service_name)).all()
    try:
        with configured_client(db, settings.demo_org_id) as client:
            inventory = client.list_resources()
        resources = {(item["resource_type"], item["service_name"], item.get("instance_id") or ""): item for item in inventory["items"]}
        provider_error = "; ".join(inventory["errors"])
    except OvhError as exc:
        resources = {}
        provider_error = str(exc)
    items = []
    for link in links:
        live = resources.get((link.resource_type, link.service_name, link.instance_id))
        items.append({"id": link.id, "proxy_group": link.proxy_group, "resource_type": link.resource_type,
                      "service_name": link.service_name, "instance_id": link.instance_id or None,
                      "server_name": live.get("name") if live else link.service_name,
                      "status": live.get("status") if live else ("UNAVAILABLE" if provider_error else "MISSING"),
                      "ip": live.get("ip") if live else None})
    return {"items": items, "provider_error": provider_error or None}


@app.post("/api/v1/infrastructure/ovh/proxy-groups", status_code=status.HTTP_201_CREATED)
def create_ovh_proxy_group_link(payload: OvhProxyGroupLinkRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    group_name = " ".join(payload.proxy_group.split())
    group_exists = db.scalar(select(ProxyInventory.id).where(ProxyInventory.organization_id == settings.demo_org_id, ProxyInventory.proxy_group == group_name).limit(1))
    if not group_exists:
        raise HTTPException(status_code=422, detail="Choose a proxy group that currently exists.")
    instance_id = payload.instance_id or ""
    try:
        with configured_client(db, settings.demo_org_id) as client:
            live_inventory = client.list_resources()
    except OvhError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    exists = any(item["resource_type"] == payload.resource_type and item["service_name"] == payload.service_name and (item.get("instance_id") or "") == instance_id for item in live_inventory["items"])
    if not exists:
        raise HTTPException(status_code=404, detail="The selected OVH server or instance is not in the current inventory.")
    existing = db.scalar(select(OvhProxyGroupLink).where(OvhProxyGroupLink.organization_id == settings.demo_org_id, OvhProxyGroupLink.proxy_group == group_name, OvhProxyGroupLink.resource_type == payload.resource_type, OvhProxyGroupLink.service_name == payload.service_name, OvhProxyGroupLink.instance_id == instance_id))
    if existing:
        return {"id": existing.id, "proxy_group": existing.proxy_group, "resource_type": existing.resource_type, "service_name": existing.service_name, "instance_id": existing.instance_id or ""}
    link = OvhProxyGroupLink(id=f"ovh_group_{uuid.uuid4().hex}", organization_id=settings.demo_org_id, proxy_group=group_name, resource_type=payload.resource_type, service_name=payload.service_name, instance_id=instance_id)
    db.add(link)
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex}", organization_id=settings.demo_org_id, actor_id=admin.employee_id, action="ovh.proxy_group.linked", target_type="proxy_group", target_id=group_name, result="success", metadata_json={"resource_type": payload.resource_type, "service_name": payload.service_name, "instance_id": instance_id or None}))
    db.commit()
    return {"id": link.id, "proxy_group": link.proxy_group, "resource_type": link.resource_type, "service_name": link.service_name, "instance_id": link.instance_id or ""}


@app.delete("/api/v1/infrastructure/ovh/proxy-groups/{link_id}")
def delete_ovh_proxy_group_link(link_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    link = db.scalar(select(OvhProxyGroupLink).where(OvhProxyGroupLink.organization_id == settings.demo_org_id, OvhProxyGroupLink.id == link_id))
    if not link:
        raise HTTPException(status_code=404, detail="Resource not found")
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex}", organization_id=settings.demo_org_id, actor_id=admin.employee_id, action="ovh.proxy_group.unlinked", target_type="proxy_group", target_id=link.proxy_group, result="success", metadata_json={"resource_type": link.resource_type, "service_name": link.service_name, "instance_id": link.instance_id or None}))
    db.delete(link)
    db.commit()
    return {"deleted": True}


@app.post("/api/v1/infrastructure/ovh/power", status_code=status.HTTP_202_ACCEPTED)
def ovh_power(payload: OvhPowerRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    target_recovery = payload.resource_type == "instance" and bool(payload.instance_id) and recovery_target(payload.service_name, payload.instance_id or "")
    recovery_job = None
    recovery_warning = None
    try:
        if payload.action == "stop" and target_recovery:
            if not request_recovery_cancel(settings.demo_org_id, payload.service_name, payload.instance_id or ""):
                raise HTTPException(status_code=409, detail="Proxy recovery is finishing an in-flight restore. Retry Stop shortly; the VM was not stopped.")
        if payload.action == "start" and target_recovery:
            configured, reason = recovery_configured()
            if not configured:
                recovery_warning = f"Proxy verification was skipped: {reason} The instance can start, but proxies are not verified or ready."
            else:
                try:
                    validate_openstack_config()
                except RecoveryError as exc:
                    recovery_warning = f"Proxy verification was skipped: {exc} The instance can start, but proxies are not verified or ready."
            if not recovery_warning:
                try:
                    recovery_job = create_recovery_job(settings.demo_org_id, payload.service_name, payload.instance_id or "", admin.employee_id, start=False)
                except RecoveryError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
        with configured_client(db, settings.demo_org_id) as client:
            client.power(payload.resource_type, payload.service_name, payload.instance_id, payload.action)
        record_power_action(db, settings.demo_org_id, admin.employee_id, payload.resource_type, payload.service_name, payload.instance_id, payload.action)
        if recovery_job:
            start_recovery_thread(recovery_job["id"])
        return {"status": "accepted", "resource_type": payload.resource_type, "service_name": payload.service_name, "instance_id": payload.instance_id, "action": payload.action, "recovery_job": recovery_job, "recovery_warning": recovery_warning}
    except OvhError as exc:
        if recovery_job:
            fail_recovery_job(recovery_job["id"], "OVH rejected the Start request; no proxy recovery was performed.")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        if recovery_job:
            fail_recovery_job(recovery_job["id"], "The Start request failed; no proxy recovery was performed.")
        logger.error("OVH power action failed; exception detail redacted")
        raise HTTPException(status_code=502, detail="OVH power action failed. Check API service logs.") from exc


@app.post("/api/v1/infrastructure/ovh/recovery/start", status_code=status.HTTP_202_ACCEPTED)
def ovh_start_proxy_recovery(request: Request, db: Session = Depends(get_db)) -> dict:
    actor = _account_operator(request, db)
    project_id = settings.ovh_proxy_recovery_project_id
    instance_id = settings.ovh_proxy_recovery_instance_id
    if not recovery_target(project_id, instance_id):
        raise HTTPException(status_code=409, detail="Floating IP recovery is disabled for this instance.")
    configured, reason = recovery_configured()
    if not configured:
        raise HTTPException(status_code=422, detail=f"Proxy recovery is unavailable: {reason}")
    try:
        validate_openstack_config()
        with configured_client(db, settings.demo_org_id) as client:
            items = client.list_resources().get("items", [])
        target = next((item for item in items if item.get("resource_type") == "instance" and item.get("service_name") == project_id and item.get("instance_id") == instance_id), None)
        if target is None or str(target.get("status", "")).upper() not in {"ACTIVE", "RUNNING"}:
            raise HTTPException(status_code=409, detail="Start the configured OVH instance before verifying its proxies.")
        job = create_recovery_job(settings.demo_org_id, project_id, instance_id, actor.employee_id)
    except RecoveryError as exc:
        raise HTTPException(status_code=409 if "already active" in str(exc).lower() else 422, detail=str(exc)) from exc
    except OvhError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return job


@app.get("/api/v1/infrastructure/ovh/recovery/latest")
def ovh_latest_recovery(service_name: str, instance_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_operator(request, db)
    job = latest_recovery_job(settings.demo_org_id, service_name, instance_id)
    if not job:
        raise HTTPException(status_code=404, detail="No recovery job was found for this instance.")
    return job


@app.get("/api/v1/infrastructure/ovh/recovery/{job_id}")
def ovh_recovery_status(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_operator(request, db)
    job = get_recovery_job(settings.demo_org_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Recovery job not found.")
    return job


@app.get("/api/v1/infrastructure/godaddy/accounts")
def godaddy_accounts(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    _account_operator(request, db)
    return list_godaddy_accounts(db, settings.demo_org_id)


@app.post("/api/v1/infrastructure/godaddy/accounts", status_code=status.HTTP_201_CREATED)
def godaddy_account_create(payload: GoDaddyAccountCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    try:
        return save_godaddy_account(db, settings.demo_org_id, admin.employee_id, **payload.model_dump())
    except GoDaddyError as exc:
        raise HTTPException(status_code=exc.status_code or 422, detail=str(exc)) from exc


@app.put("/api/v1/infrastructure/godaddy/accounts/{account_id}")
def godaddy_account_update(account_id: str, payload: GoDaddyAccountUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    try:
        return save_godaddy_account(db, settings.demo_org_id, admin.employee_id, account_id=account_id, **payload.model_dump())
    except GoDaddyError as exc:
        raise HTTPException(status_code=exc.status_code or (404 if "not found" in str(exc).lower() else 422), detail=str(exc)) from exc


@app.delete("/api/v1/infrastructure/godaddy/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def godaddy_account_delete(account_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    admin = _account_operator(request, db)
    try:
        remove_godaddy_account(db, settings.demo_org_id, admin.employee_id, account_id)
    except GoDaddyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/infrastructure/godaddy/accounts/{account_id}/test")
def godaddy_account_test(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_operator(request, db)
    try:
        with godaddy_client(db, settings.demo_org_id, account_id) as client:
            domains = client.list_domains()
        return {"connected": True, "domain_count": len(domains)}
    except GoDaddyError as exc:
        raise HTTPException(status_code=exc.status_code or 422, detail=str(exc)) from exc


@app.get("/api/v1/infrastructure/godaddy/accounts/{account_id}/domains")
def godaddy_domains(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_operator(request, db)
    try:
        with godaddy_client(db, settings.demo_org_id, account_id) as client:
            domains = client.list_domains()
        safe_domains = [{key: domain.get(key) for key in ("domain", "status", "expires", "renewAuto", "nameServers")} for domain in domains]
        return {"items": safe_domains, "total": len(safe_domains)}
    except GoDaddyError as exc:
        raise HTTPException(status_code=exc.status_code or 422, detail=str(exc)) from exc


@app.get("/api/v1/infrastructure/godaddy/accounts/{account_id}/domains/{domain}/dns")
def godaddy_dns_records(account_id: str, domain: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_operator(request, db)
    try:
        with godaddy_client(db, settings.demo_org_id, account_id) as client:
            records = client.list_records(domain)
        return {"items": records, "total": len(records)}
    except GoDaddyError as exc:
        logger.warning("GoDaddy DNS retrieval failed for account %s domain %s: %s", account_id, domain, exc)
        raise HTTPException(status_code=exc.status_code or 422, detail=str(exc)) from exc


@app.post("/api/v1/infrastructure/godaddy/accounts/{account_id}/domains/{domain}/dns", status_code=status.HTTP_201_CREATED)
def godaddy_dns_record_create(account_id: str, domain: str, payload: GoDaddyDnsRecord, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    try:
        body = payload.model_dump(exclude_none=True)
        with godaddy_client(db, settings.demo_org_id, account_id) as client:
            client.add_record(domain, body)
        _record_godaddy_dns_audit(db, admin.employee_id, account_id, domain, "created", body)
        return {"status": "created", "record": body}
    except GoDaddyError as exc:
        raise HTTPException(status_code=exc.status_code or 422, detail=str(exc)) from exc


@app.put("/api/v1/infrastructure/godaddy/accounts/{account_id}/domains/{domain}/dns/{record_type}/{record_name}")
def godaddy_dns_group_replace(account_id: str, domain: str, record_type: str, record_name: str, payload: GoDaddyDnsGroupReplace, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    try:
        records = [record.model_dump(exclude_none=True) for record in payload.records]
        with godaddy_client(db, settings.demo_org_id, account_id) as client:
            client.replace_group(domain, record_type, record_name, records)
        _record_godaddy_dns_audit(db, admin.employee_id, account_id, domain, "replaced", {"type": record_type.upper(), "name": record_name, "record_count": len(records)})
        return {"status": "replaced", "record_count": len(records)}
    except GoDaddyError as exc:
        raise HTTPException(status_code=exc.status_code or 422, detail=str(exc)) from exc


@app.delete("/api/v1/infrastructure/godaddy/accounts/{account_id}/domains/{domain}/dns/{record_type}/{record_name}", status_code=status.HTTP_204_NO_CONTENT)
def godaddy_dns_group_delete(account_id: str, domain: str, record_type: str, record_name: str, request: Request, db: Session = Depends(get_db)) -> None:
    admin = _account_operator(request, db)
    try:
        with godaddy_client(db, settings.demo_org_id, account_id) as client:
            client.delete_group(domain, record_type, record_name)
        _record_godaddy_dns_audit(db, admin.employee_id, account_id, domain, "deleted", {"type": record_type.upper(), "name": record_name})
    except GoDaddyError as exc:
        raise HTTPException(status_code=exc.status_code or 422, detail=str(exc)) from exc


def _record_godaddy_dns_audit(db: Session, actor_id: str, account_id: str, domain: str, operation: str, metadata: dict) -> None:
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, actor_id=actor_id, action=f"godaddy.dns_{operation}", target_type="dns_zone", target_id=f"{account_id}:{domain}", result="success", metadata_json=metadata))
    db.commit()


@app.post("/api/v1/notifications/test", status_code=status.HTTP_204_NO_CONTENT)
def notification_test(payload: NotificationTestRequest, request: Request, db: Session = Depends(get_db)) -> None:
    admin = _admin(request, db)
    try:
        send_test(db, settings.demo_org_id, payload.channel)
    except NotificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, actor_id=admin.employee_id, action="notifications.tested", target_type="workspace_notification_settings", target_id=settings.demo_org_id, result="success", metadata_json={"channel": payload.channel}))
    db.commit()


@app.post("/api/v1/notifications/status-report", status_code=status.HTTP_204_NO_CONTENT)
def notification_status_report(group_id: str | None = None, request: Request = None, db: Session = Depends(get_db)) -> None:
    admin = _admin(request, db)
    if group_id and not admin.is_admin and not admin.can_access_group(group_id, "VIEWER"):
        raise HTTPException(status_code=404, detail="Resource not found")
    send_group_status_report(db, settings.demo_org_id, group_id)


@app.get("/api/v1/accounts", response_model=AccountListResponse)
def accounts(q: str | None = Query(default=None, max_length=320), group_id: str | None = None, status_filter: str | None = Query(default=None, alias="status"), page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=10000), request: Request = None, db: Session = Depends(get_db)) -> dict:
    visible = _visible_groups(request, db)
    if visible is not None and group_id and group_id not in visible:
        raise HTTPException(status_code=404, detail="Resource not found")
    return list_accounts(db, settings.demo_org_id, q, group_id, status_filter, page, page_size, visible_group_ids=visible)


@app.post("/api/v1/profiles/compact")
def compact_profiles(payload: ProfileCompactRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    operator = _account_operator(request, db)
    accounts = db.scalars(select(GmailAccount).where(
        GmailAccount.organization_id == settings.demo_org_id,
        GmailAccount.id.in_(payload.account_ids),
        GmailAccount.lifecycle_status != "ARCHIVED",
        GmailAccount.account_type != "EXTERNAL_RECIPIENT",
    )).all()
    if len(accounts) != len(payload.account_ids) or any(not operator.can_access_group(account.group_id, "OPERATOR") for account in accounts):
        raise HTTPException(status_code=404, detail="Account not found")
    results = [compact_account_profile(db, settings.demo_org_id, operator.employee_id, account_id) for account_id in payload.account_ids]
    return {"results": results, "removed_bytes": sum(int(row["removed_bytes"]) for row in results)}


@app.post("/api/v1/profiles/sizes")
def get_profile_sizes(payload: ProfileSizesRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    operator = _account_operator(request, db)
    accounts = db.scalars(select(GmailAccount).where(
        GmailAccount.organization_id == settings.demo_org_id,
        GmailAccount.id.in_(payload.account_ids),
        GmailAccount.lifecycle_status != "ARCHIVED",
        GmailAccount.account_type != "EXTERNAL_RECIPIENT",
    )).all()
    by_id = {account.id: account for account in accounts}
    if len(by_id) != len(payload.account_ids) or any(not operator.can_access_group(account.group_id, "OPERATOR") for account in accounts):
        raise HTTPException(status_code=404, detail="Account not found")
    root = Path(settings.profile_root).resolve()
    items = []
    for account_id in payload.account_ids:
        account = by_id[account_id]
        profile_id = account.profile_id or (account.browser_profile.id if account.browser_profile else None)
        try:
            size_bytes = profile_size_bytes(profile_path(root, profile_id))
        except (OSError, ValueError):
            size_bytes = None
        items.append({"account_id": account_id, "size_bytes": size_bytes})
    return {"items": items}


@app.post("/api/v1/profiles/export")
def export_account_profiles(payload: ProfileExportRequest, request: Request, db: Session = Depends(get_db)) -> FileResponse:
    operator = _account_operator(request, db)
    accounts = db.scalars(select(GmailAccount).where(
        GmailAccount.organization_id == settings.demo_org_id,
        GmailAccount.id.in_(payload.account_ids),
        GmailAccount.lifecycle_status != "ARCHIVED",
        GmailAccount.account_type != "EXTERNAL_RECIPIENT",
    ).with_for_update(of=GmailAccount)).all()
    by_id = {account.id: account for account in accounts}
    if len(by_id) != len(payload.account_ids) or any(not operator.can_access_group(account.group_id, "OPERATOR") for account in accounts):
        raise HTTPException(status_code=404, detail="Account not found")
    root = Path(settings.profile_root).resolve()
    profiles: list[tuple[str, str]] = []
    profile_ids: set[str] = set()
    for account_id in payload.account_ids:
        account = by_id[account_id]
        profile_id = account.profile_id or (account.browser_profile.id if account.browser_profile else None)
        if not profile_path(root, profile_id):
            raise HTTPException(status_code=409, detail=f"No saved browser profile is available for {account.email}.")
        profiles.append((account.email, str(profile_id)))
        profile_ids.add(str(profile_id))
    active = db.scalar(select(BrowserSession.id).where(
        BrowserSession.account_id.in_(payload.account_ids),
        BrowserSession.state.in_({"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}),
        BrowserSession.closed_at.is_(None),
    ).limit(1))
    if active or local_browser_profiles_alive(profile_ids):
        raise HTTPException(status_code=409, detail="Close the selected account browsers before exporting profiles so the archive is complete and consistent.")
    if any(account.browser_profile and (account.browser_profile.lock_state == "LOCKED" or account.browser_profile.active_session_id) for account in by_id.values()):
        raise HTTPException(status_code=409, detail="A selected profile is locked by another operation. Retry after it completes.")
    if not _BACKUP_OPERATION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another backup or profile transfer is running. Retry when it finishes.")
    temporary: Path | None = None
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="northstar-profile-export-", dir=root.parent))
        temporary.chmod(0o700)
        archive_path = temporary / "northstar-profiles.zip"
        archive_path.touch(mode=0o600)
        try:
            file_count, byte_count = write_profiles_zip(root, profiles, archive_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, actor_id=operator.employee_id,
            action="browser_profile.exported", target_type="browser_profiles", target_id=operator.employee_id, result="success",
            metadata_json={"account_ids": payload.account_ids, "profile_count": len(profiles), "file_count": file_count, "size_bytes": byte_count}))
        db.commit()
        filename = f"northstar-profiles-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.zip"
        response = FileResponse(archive_path, media_type="application/zip", filename=filename,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True))
        temporary = None
        return response
    finally:
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)
        _BACKUP_OPERATION_LOCK.release()


@app.get("/api/v1/profiles/compact-targets")
def compact_profile_targets(request: Request, db: Session = Depends(get_db)) -> dict:
    operator = _account_operator(request, db)
    query = select(GmailAccount.id, GmailAccount.email).where(
        GmailAccount.organization_id == settings.demo_org_id,
        GmailAccount.lifecycle_status != "ARCHIVED",
        GmailAccount.account_type != "EXTERNAL_RECIPIENT",
    )
    if not operator.is_admin:
        group_ids = [group_id for group_id in operator.group_access if operator.can_access_group(group_id, "OPERATOR")]
        query = query.where(GmailAccount.group_id.in_(group_ids or ["__no_access__"]))
    rows = db.execute(query.order_by(GmailAccount.email)).all()
    return {"items": [{"id": account_id, "email": email} for account_id, email in rows], "total": len(rows)}


@app.get("/api/v1/accounts/{account_id}", response_model=AccountResponse)
def account_detail(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    account = _account_access(request, db, account_id)
    return account_response(account)


@app.get("/api/v1/accounts/{account_id}/newsletter-identity", response_model=NewsletterAccountIdentityResponse)
def newsletter_identity_detail(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    return get_newsletter_account_identity(db, settings.demo_org_id, account_id)


@app.put("/api/v1/accounts/{account_id}/newsletter-identity", response_model=NewsletterAccountIdentityResponse)
def newsletter_identity_update(account_id: str, payload: NewsletterAccountIdentityUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    try:
        return update_newsletter_account_identity(db, settings.demo_org_id, _actor(request, db), account_id, payload.values)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/newsletter-identity/generate", response_model=NewsletterIdentityGenerateResponse)
def newsletter_identity_generate(account_id: str, payload: NewsletterIdentityGenerateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    try:
        return generate_newsletter_account_identity(
            db,
            settings.demo_org_id,
            _actor(request, db),
            account_id,
            commit=payload.commit,
            overrides=payload.overrides,
        )
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/accounts/single", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountStructuredInput, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return account_response(add_account(db, settings.demo_org_id, admin.employee_id, payload))
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/v1/accounts/{account_id}", response_model=AccountResponse)
def edit_account_metadata(account_id: str, payload: AccountMetadataUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        proxy_value = payload.proxy if "proxy" in payload.model_fields_set else _UNSET
        return update_account_metadata(db, settings.demo_org_id, "admin_demo", account_id, payload.proxy_label, payload.proxy_region, proxy_value)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc)) from exc


@app.patch("/api/v1/accounts/{account_id}/tags", response_model=AccountResponse)
def edit_account_tags(account_id: str, payload: AccountTagsUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    try:
        return update_account_tags(db, settings.demo_org_id, _actor(request, db), account_id, payload.tags)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/group-move", response_model=AccountResponse)
def move_account(account_id: str, payload: GroupMoveRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    account = _account_access(request, db, account_id, "OPERATOR")
    user = _current_user(request, db)
    if not user.is_admin and not user.can_access_group(payload.destination_group_id, "OPERATOR"):
        raise HTTPException(status_code=404, detail="Resource not found")
    try:
        return move_account_group(db, settings.demo_org_id, user.employee_id, account.id, payload.destination_group_id)
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=404 if "not found" in message or "valid destination" in message else 409, detail=str(exc)) from exc


@app.delete("/api/v1/accounts/{account_id}", response_model=AccountResponse)
def delete_account(account_id: str, payload: AccountDeleteRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return archive_account(db, settings.demo_org_id, "admin_demo", account_id, payload.confirmation_email)
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=404 if "not found" in message else 409, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/operation-drafts", response_model=OperationDraftResponse, status_code=status.HTTP_201_CREATED)
def save_operation_draft(account_id: str, payload: OperationDraftCreate, db: Session = Depends(get_db)) -> dict:
    try:
        return create_operation_draft(db, settings.demo_org_id, "admin_demo", account_id, payload.operation_type, payload.configuration)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.get("/api/v1/accounts/{account_id}/operation-drafts", response_model=list[OperationDraftResponse])
def operation_drafts(account_id: str, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return list_operation_drafts(db, settings.demo_org_id, account_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/accounts/{account_id}/preferences", response_model=AccountPreferencesResponse)
def account_preferences(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    try:
        return get_account_preferences(db, settings.demo_org_id, account_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/v1/accounts/{account_id}/preferences", response_model=AccountPreferencesResponse)
def save_account_preferences(account_id: str, payload: AccountPreferencesUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    try:
        return update_account_preferences(db, settings.demo_org_id, _actor(request, db), account_id, payload.gmail_theme, payload.avatar_filename)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/preferences/avatar", response_model=AccountPreferencesResponse)
async def upload_account_preferences_avatar(account_id: str, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    try:
        content = await _read_upload_bounded(file, 5 * 1024 * 1024)
        return upload_account_preference_avatar(db, settings.demo_org_id, _actor(request, db), account_id, file.filename or "", content)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/preferences/apply", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def apply_account_preferences(account_id: str, request: Request, payload: AccountPreferencesApplyRequest | None = None, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "OPERATOR")
    try:
        options = payload or AccountPreferencesApplyRequest()
        return session_response(start_account_preferences_operation(db, settings.demo_org_id, _actor(request, db), account_id, change_avatar=options.change_avatar, change_theme=options.change_theme, random_theme=options.random_theme))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/bulk/preview", response_model=BulkPreviewResponse)
def bulk_preview(payload: BulkPreviewRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _authorize_group(request, payload.group_id, "OPERATOR")
    try:
        return preview_import(db, settings.demo_org_id, payload.group_id, payload.raw_lines)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/bulk/import", response_model=ImportSummary, status_code=status.HTTP_201_CREATED)
def bulk_import(payload: BulkImportRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _authorize_group(request, payload.group_id, "OPERATOR")
    try:
        imported, duplicates, invalid, ids = import_accounts(db, settings.demo_org_id, _actor(request, db), payload.group_id, payload.raw_lines, payload.idempotency_key)
        return {"imported": imported, "duplicates": duplicates, "invalid": invalid, "account_ids": ids}
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/v1/accounts/{account_id}/credentials", response_model=AccountResponse)
def replace_account_credentials(account_id: str, payload: CredentialUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "MANAGER")
    try:
        return update_credentials(db, settings.demo_org_id, _actor(request, db), account_id, payload.password, payload.recovery, payload.two_fa)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/credentials/rotate", response_model=CredentialRotateResponse)
def rotate_account_credential(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _account_access(request, db, account_id, "MANAGER")
    try:
        return rotate_account_credentials(db, settings.demo_org_id, _actor(request, db), account_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.get("/api/v1/accounts/{account_id}/credentials/plaintext", response_model=CredentialPlaintextResponse)
def account_credential_plaintext(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    _account_access(request, db, account_id, "MANAGER")
    try:
        return credential_plaintext(db, settings.demo_org_id, "admin_demo", account_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.get("/api/v1/credentials", response_model=CredentialRecordListResponse)
def list_credential_records(q: str | None = None, group_id: str | None = None, status: str | None = None, page: int = 1, page_size: int = 50, request: Request = None, db: Session = Depends(get_db)) -> dict:
    return credential_records(db, settings.demo_org_id, q, group_id, status, page, page_size, visible_group_ids=_visible_groups(request, db))


@app.get("/api/v1/accounts/{account_id}/credential-revisions", response_model=list[CredentialRevisionResponse])
def account_credential_revisions(account_id: str, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return credential_revisions(db, settings.demo_org_id, account_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/credential-change-plans", response_model=CredentialChangePlanResponse, status_code=status.HTTP_201_CREATED)
def create_account_credential_change_plan(account_id: str, payload: CredentialChangePlanCreate, db: Session = Depends(get_db)) -> dict:
    try:
        return create_credential_change_plan(db, settings.demo_org_id, "admin_demo", account_id, payload.password, payload.recovery, payload.two_fa, payload.recovery_emails)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.get("/api/v1/accounts/{account_id}/credential-change-plans", response_model=list[CredentialChangePlanResponse])
def account_credential_change_plans(account_id: str, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return list_credential_change_plans(db, settings.demo_org_id, account_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/credential-change-plans/{plan_id}/start", response_model=CredentialChangePlanResponse)
def start_account_credential_change_plan(plan_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return start_credential_change_plan(db, settings.demo_org_id, _actor(request, db), plan_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.post("/api/v1/credential-change-plans/{plan_id}/confirm", response_model=CredentialChangePlanResponse)
def confirm_account_credential_change_plan(plan_id: str, payload: CredentialChangePlanConfirm, db: Session = Depends(get_db)) -> dict:
    try:
        return confirm_credential_change_plan(db, settings.demo_org_id, "admin_demo", plan_id, payload.acknowledged, payload.password, payload.two_fa, payload.recovery)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.post("/api/v1/credential-change-plans/{plan_id}/cancel", response_model=CredentialChangePlanResponse)
def cancel_account_credential_change_plan(plan_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return cancel_credential_change_plan(db, settings.demo_org_id, "admin_demo", plan_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs", response_model=LoginJobResponse, status_code=status.HTTP_201_CREATED)
def create_bulk_login_job(payload: LoginJobCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _account_operator(request, db)
        return create_login_job(db, settings.demo_org_id, user.employee_id, payload.group_id, payload.account_ids, payload.concurrency_limit)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/login-jobs", response_model=list[LoginJobResponse])
def login_jobs(limit: int = Query(default=25, ge=1, le=100), request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    user = _current_user(request, db)
    return list_login_jobs(db, settings.demo_org_id, limit, created_by=user.employee_id)


def _operable_login_job(job_id: str, request: Request, db: Session):
    user = _current_user(request, db)
    job = db.scalar(select(LoginJob).where(LoginJob.organization_id == settings.demo_org_id, LoginJob.id == job_id, LoginJob.created_by == user.employee_id))
    if not job:
        raise HTTPException(status_code=404, detail="Login batch not found")
    if job.group_id and not user.can_access_group(job.group_id, "OPERATOR"):
        raise HTTPException(status_code=404, detail="Login batch not found")
    return user


@app.get("/api/v1/login-jobs/{job_id}", response_model=LoginJobResponse)
def login_job_detail(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _operable_login_job(job_id, request, db)
    job = get_login_job(db, settings.demo_org_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Login batch not found")
    return job


@app.post("/api/v1/login-jobs/{job_id}/start", response_model=LoginJobResponse)
def start_bulk_login_job(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        update_login_job_state(db, settings.demo_org_id, user.employee_id, job_id, "RUNNING")
        return dispatch_login_job(db, settings.demo_org_id, user.employee_id, job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/pause", response_model=LoginJobResponse)
def pause_bulk_login_job(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        return update_login_job_state(db, settings.demo_org_id, user.employee_id, job_id, "PAUSED")
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/dispatch", response_model=LoginJobResponse)
def dispatch_bulk_login_job(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        return dispatch_login_job(db, settings.demo_org_id, user.employee_id, job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "start" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.get("/api/v1/login-jobs/{job_id}/progress", response_model=LoginJobProgressResponse)
def login_job_progress_endpoint(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _operable_login_job(job_id, request, db)
    progress = login_job_progress(db, settings.demo_org_id, job_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Login batch not found")
    return progress


@app.post("/api/v1/login-jobs/{job_id}/dispatch-next", response_model=LoginJobResponse)
def dispatch_next_login_job(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        return dispatch_login_job(db, settings.demo_org_id, user.employee_id, job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "start" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/resume", response_model=LoginJobResponse)
def resume_bulk_login_job(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        return resume_login_job(db, settings.demo_org_id, user.employee_id, job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "resume" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/cancel", response_model=LoginJobResponse)
def cancel_bulk_login_job(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        return update_login_job_state(db, settings.demo_org_id, user.employee_id, job_id, "CANCELLED")
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Newsletter automation (registry, single-account tests, bulk jobs, and health recovery)
# ---------------------------------------------------------------------------


@app.get("/api/v1/newsletter-groups", response_model=list[NewsletterGroupResponse])
def newsletter_groups(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    _current_user(request, db)
    return list_newsletter_groups(db, settings.demo_org_id)


@app.post("/api/v1/newsletter-groups/seed-directory", response_model=NewsletterDirectorySeedResponse)
def seed_newsletter_directory_route(request: Request, db: Session = Depends(get_db)) -> dict:
    """Seed the curated topic directory; this never discovers or subscribes."""
    admin = _admin(request, db)
    return seed_newsletter_directory(db, settings.demo_org_id, admin.employee_id)


@app.post("/api/v1/newsletters/import-catalog", response_model=NewsletterCatalogImportResponse)
def import_newsletter_catalog_route(request: Request, db: Session = Depends(get_db)) -> dict:
    """Import the supplied five-bucket catalog without discovering or subscribing."""
    admin = _admin(request, db)
    return import_newsletter_catalog(db, settings.demo_org_id, admin.employee_id)


@app.post("/api/v1/newsletter-groups", response_model=NewsletterGroupResponse, status_code=status.HTTP_201_CREATED)
def create_newsletter_group_route(payload: NewsletterGroupCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return create_newsletter_group(db, settings.demo_org_id, admin.employee_id, payload.name, payload.description)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletter-groups/{group_id}", response_model=NewsletterGroupResponse)
def newsletter_group_detail(group_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _current_user(request, db)
    group = get_newsletter_group(db, settings.demo_org_id, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Newsletter group not found")
    return group


@app.patch("/api/v1/newsletter-groups/{group_id}", response_model=NewsletterGroupResponse)
def edit_newsletter_group(group_id: str, payload: NewsletterGroupUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return update_newsletter_group(db, settings.demo_org_id, admin.employee_id, group_id, payload.name, payload.description, payload.status)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-groups/{group_id}/disable", response_model=NewsletterGroupResponse)
def disable_newsletter_group_route(group_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return disable_newsletter_group(db, settings.demo_org_id, admin.employee_id, group_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-groups/{group_id}/enable", response_model=NewsletterGroupResponse)
def enable_newsletter_group_route(group_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return enable_newsletter_group(db, settings.demo_org_id, admin.employee_id, group_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletters", response_model=list[NewsletterResponse])
def newsletters(group_id: str | None = None, request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    _current_user(request, db)
    return list_newsletters(db, settings.demo_org_id, group_id)


@app.post("/api/v1/newsletters", response_model=NewsletterResponse, status_code=status.HTTP_201_CREATED)
def create_newsletter_route(payload: NewsletterCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return create_newsletter(
            db,
            settings.demo_org_id,
            admin.employee_id,
            payload.group_id,
            payload.name,
            payload.website_url,
            payload.signup_url,
            payload.confirmation_required,
            payload.allowed_confirmation_domains,
            payload.sender_patterns,
            payload.subject_patterns,
            payload.discovery_status,
        )
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletters/{newsletter_id}", response_model=NewsletterResponse)
def newsletter_detail(newsletter_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _current_user(request, db)
    newsletter = get_newsletter(db, settings.demo_org_id, newsletter_id)
    if not newsletter:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    return newsletter


@app.get("/api/v1/newsletters/{newsletter_id}/health", response_model=NewsletterHealthResponse)
def newsletter_health(newsletter_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _current_user(request, db)
    try:
        return newsletter_automation_health_response(db, settings.demo_org_id, newsletter_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


def _newsletter_discovery_outcome(result: dict) -> tuple[str, str | None]:
    """Classify discovery using factual reachability and candidate evidence."""
    if result.get("discovery_blocked"):
        reasons = [str(item).strip() for item in (result.get("blocked_reasons") or []) if str(item).strip()]
        return "DISCOVERY_BLOCKED", ("Discovery blocked: " + ", ".join(reasons))[:300] if reasons else "Discovery was blocked before a safe signup form could be approved."
    if not result.get("website_reachable"):
        return "FAILED", "The configured public page was not reachable."
    if not result.get("candidates"):
        return "FAILED", "No safe newsletter signup form candidate was found."
    return "DISCOVERED", None


def _discover_and_record_newsletter(db: Session, actor_id: str, newsletter: dict) -> dict:
    result = discover_newsletter(
        newsletter.get("website_url"),
        newsletter.get("signup_url"),
        max_stealth_fetches=settings.newsletter_discovery_stealth_max_fetches,
    )
    result["newsletter_id"] = newsletter["id"]
    discovery_status, failure_reason = _newsletter_discovery_outcome(result)
    record_newsletter_discovery(
        db,
        settings.demo_org_id,
        actor_id,
        newsletter["id"],
        discovery_status,
        int(result.get("inspected_url_count", 0)),
        len(result.get("candidates", [])),
        failure_reason,
    )
    return result


def _newsletter_bulk_discovery_item(newsletter: dict, result: dict | None = None) -> dict:
    return {
        "newsletter_id": newsletter["id"],
        "name": newsletter["name"],
        "website_url": newsletter.get("website_url"),
        "discovery_status": newsletter["discovery_status"],
        "registry_status": newsletter["registry_status"],
        "failure_reason": newsletter.get("failure_reason"),
        "inspected_url_count": max(0, min(int((result or {}).get("inspected_url_count", 0)), 8)),
        "candidate_count": max(0, min(len((result or {}).get("candidates", [])), 6)),
    }


@app.post("/api/v1/newsletters/discover-all", response_model=NewsletterBulkDiscoveryResponse)
def discover_all_newsletters_route(request: Request, db: Session = Depends(get_db)) -> dict:
    """Discover every configured registry website sequentially and independently."""
    admin = _admin(request, db)
    registry = list_newsletters(db, settings.demo_org_id)
    started_at = datetime.now(timezone.utc)
    items: list[dict] = []

    for newsletter in registry:
        result: dict | None = None
        try:
            result = _discover_and_record_newsletter(db, admin.employee_id, newsletter)
        except NewsletterDiscoveryError as exc:
            reason = str(exc)[:300]
            record_newsletter_discovery(db, settings.demo_org_id, admin.employee_id, newsletter["id"], "FAILED", 0, 0, reason)
            logger.info("newsletter bulk discovery failed for %s: %s", newsletter["id"], reason)
        except Exception:
            reason = "Unexpected discovery failure while inspecting the public page safely."
            logger.exception("newsletter bulk discovery failed unexpectedly for %s", newsletter["id"])
            record_newsletter_discovery(db, settings.demo_org_id, admin.employee_id, newsletter["id"], "FAILED", 0, 0, reason)

        stored = get_newsletter(db, settings.demo_org_id, newsletter["id"])
        if stored:
            items.append(_newsletter_bulk_discovery_item(stored, result))

    completed_at = datetime.now(timezone.utc)
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "requested_count": len(registry),
        "completed_count": len(items),
        "skipped_count": len(registry) - len(items),
        "discovered_count": sum(item["discovery_status"] == "DISCOVERED" for item in items),
        "blocked_count": sum(item["discovery_status"] == "DISCOVERY_BLOCKED" for item in items),
        "failed_count": sum(item["discovery_status"] == "FAILED" for item in items),
        "items": items,
    }


@app.post("/api/v1/newsletters/{newsletter_id}/discover", response_model=NewsletterDiscoveryResponse)
def discover_newsletter_route(newsletter_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Inspect only public newsletter pages and return reviewable candidates."""
    admin = _admin(request, db)
    newsletter = get_newsletter(db, settings.demo_org_id, newsletter_id)
    if not newsletter:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    try:
        return _discover_and_record_newsletter(db, admin.employee_id, newsletter)
    except NewsletterDiscoveryError as exc:
        record_newsletter_discovery(db, settings.demo_org_id, admin.employee_id, newsletter_id, "FAILED", 0, 0, str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("newsletter discovery failed for %s: %s", newsletter_id, exc)
        record_newsletter_discovery(db, settings.demo_org_id, admin.employee_id, newsletter_id, "FAILED", 0, 0, "Unexpected discovery failure while inspecting the public page safely.")
        raise HTTPException(status_code=502, detail="The public newsletter page could not be inspected safely") from exc


@app.post("/api/v1/newsletters/{newsletter_id}/rediscover", response_model=NewsletterRediscoveryResponse)
def rediscover_newsletter_route(newsletter_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Rediscover a replacement candidate without activating it."""
    admin = _admin(request, db)
    newsletter = get_newsletter(db, settings.demo_org_id, newsletter_id)
    if not newsletter:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    if newsletter.get("status") == "DISABLED":
        raise HTTPException(status_code=409, detail="Disabled newsletters cannot be rediscovered")
    try:
        discovery = discover_newsletter(
            newsletter.get("website_url"),
            newsletter.get("signup_url"),
            max_stealth_fetches=settings.newsletter_discovery_stealth_max_fetches,
        )
        discovery["newsletter_id"] = newsletter_id
        discovery_status, failure_reason = _newsletter_discovery_outcome(discovery)
        record_newsletter_discovery(
            db,
            settings.demo_org_id,
            admin.employee_id,
            newsletter_id,
            discovery_status,
            int(discovery.get("inspected_url_count", 0)),
            len(discovery.get("candidates", [])),
            failure_reason,
        )
        candidate_profile = None
        candidates = discovery.get("candidates", [])
        if candidates:
            recommended = candidates[0]
            configuration = recommended.get("configuration") or {}
            if configuration.get("email_selector") and configuration.get("submit_selector"):
                candidate_profile = create_newsletter_profile(
                    db,
                    settings.demo_org_id,
                    admin.employee_id,
                    newsletter_id,
                    "CANDIDATE",
                    float(recommended.get("confidence", 0.0)),
                    configuration,
                )
                db.add(
                    AuditEvent(
                        id=f"audit_{secrets.token_hex(9)}",
                        organization_id=settings.demo_org_id,
                        actor_id=admin.employee_id,
                        action="newsletter.automation.rediscovered",
                        target_type="newsletter",
                        target_id=newsletter_id,
                        result="success",
                        metadata_json={
                            "candidate_profile_id": candidate_profile["id"],
                            "candidate_profile_version": candidate_profile["version"],
                            "confidence": candidate_profile["confidence"],
                            "validated_before_activation": True,
                        },
                    )
                )
                db.commit()
        return {
            "newsletter_id": newsletter_id,
            "discovery": discovery,
            "health": newsletter_automation_health_response(db, settings.demo_org_id, newsletter_id),
            "candidate_profile": candidate_profile,
        }
    except NewsletterDiscoveryError as exc:
        record_newsletter_discovery(db, settings.demo_org_id, admin.employee_id, newsletter_id, "FAILED", 0, 0, str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("newsletter rediscovery failed for %s: %s", newsletter_id, exc)
        record_newsletter_discovery(db, settings.demo_org_id, admin.employee_id, newsletter_id, "FAILED", 0, 0, "Unexpected rediscovery failure while inspecting the public page safely.")
        raise HTTPException(status_code=502, detail="The newsletter could not be rediscovered safely") from exc


@app.patch("/api/v1/newsletters/{newsletter_id}", response_model=NewsletterResponse)
def edit_newsletter(newsletter_id: str, payload: NewsletterUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return update_newsletter(
            db,
            settings.demo_org_id,
            admin.employee_id,
            newsletter_id,
            payload.group_id,
            payload.name,
            payload.website_url,
            payload.signup_url,
            payload.status,
            payload.confirmation_required,
            payload.allowed_confirmation_domains,
            payload.sender_patterns,
            payload.subject_patterns,
            payload.discovery_status,
        )
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletters/{newsletter_id}/disable", response_model=NewsletterResponse)
def disable_newsletter_route(newsletter_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return disable_newsletter(db, settings.demo_org_id, admin.employee_id, newsletter_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletters/{newsletter_id}/enable", response_model=NewsletterResponse)
def enable_newsletter_route(newsletter_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return enable_newsletter(db, settings.demo_org_id, admin.employee_id, newsletter_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletters/{newsletter_id}/skill", response_model=NewsletterSkillResponse)
def newsletter_skill_route(newsletter_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _current_user(request, db)
    try:
        get_newsletter(db, settings.demo_org_id, newsletter_id)
        return newsletter_skill_response(db, settings.demo_org_id, newsletter_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletter-skills", response_model=list[NewsletterSkillResponse])
def newsletter_skills_route(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    _current_user(request, db)
    return list_newsletter_skills(db, settings.demo_org_id)


@app.get("/api/v1/newsletter-profiles", response_model=list[NewsletterProfileResponse])
def newsletter_profiles(newsletter_id: str | None = None, request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    _current_user(request, db)
    try:
        return list_newsletter_profiles(db, settings.demo_org_id, newsletter_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-profiles", response_model=NewsletterProfileDetailResponse, status_code=status.HTTP_201_CREATED)
def create_newsletter_profile_route(payload: NewsletterProfileCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return create_newsletter_profile(
            db, settings.demo_org_id, admin.employee_id, payload.newsletter_id, payload.status, payload.confidence, payload.configuration
        )
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletter-profiles/{profile_id}", response_model=NewsletterProfileDetailResponse)
def newsletter_profile_detail(profile_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _current_user(request, db)
    profile = get_newsletter_profile_by_id(db, settings.demo_org_id, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Automation profile not found")
    return profile


@app.patch("/api/v1/newsletter-profiles/{profile_id}", response_model=NewsletterProfileDetailResponse)
def update_newsletter_profile_route(profile_id: str, payload: NewsletterProfileUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return update_newsletter_profile_by_id(db, settings.demo_org_id, admin.employee_id, profile_id, payload.configuration)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-profiles/{profile_id}/promote", response_model=NewsletterProfileDetailResponse)
def promote_newsletter_profile_route(profile_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return promote_newsletter_profile_by_id(db, settings.demo_org_id, admin.employee_id, profile_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-profiles/{profile_id}/reject", response_model=NewsletterProfileDetailResponse)
def reject_newsletter_profile_route(profile_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return reject_newsletter_profile_by_id(db, settings.demo_org_id, admin.employee_id, profile_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-profiles/{profile_id}/rollback", response_model=NewsletterProfileDetailResponse)
def rollback_newsletter_profile_route(profile_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _admin(request, db)
    try:
        return rollback_newsletter_profile_by_id(db, settings.demo_org_id, admin.employee_id, profile_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletter-subscriptions", response_model=NewsletterSubscriptionListResponse)
def newsletter_subscriptions(
    account_id: str | None = None,
    newsletter_id: str | None = None,
    state: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=200, ge=1, le=500),
    request: Request = None,
    db: Session = Depends(get_db),
) -> dict:
    _current_user(request, db)
    try:
        items, total = list_newsletter_subscriptions(
            db, settings.demo_org_id, account_id, newsletter_id, state, account_group_ids=_visible_groups(request, db), limit=limit
        )
        return {"items": items, "total": total}
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletter-subscriptions/{subscription_id}", response_model=NewsletterSubscriptionResponse)
def newsletter_subscription_detail(subscription_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _current_user(request, db)
    subscription = get_newsletter_subscription(db, settings.demo_org_id, subscription_id, account_group_ids=_visible_groups(request, db))
    if not subscription:
        raise HTTPException(status_code=404, detail="Newsletter subscription not found")
    return subscription


def _newsletter_signup_test_response(db: Session, session: BrowserSession) -> dict:
    metadata = session.metadata_json or {}
    subscription_id = str(metadata.get("newsletter_subscription_id") or "")
    subscription = db.get(NewsletterSubscription, subscription_id) if subscription_id else None
    newsletter_id = str(metadata.get("newsletter_id") or "")
    newsletter = db.get(Newsletter, newsletter_id) if newsletter_id else None
    result = str(metadata.get("newsletter_result") or (subscription.state if subscription else "IN_PROGRESS"))
    if subscription is not None and subscription.state in {"WAITING_CONFIRMATION", "CONFIRMED", "FAILED", "MANUAL_REVIEW", "SKIPPED"}:
        result = subscription.state
    if result == "PENDING":
        result = "MANUAL_REVIEW" if session.state == "NEEDS_ATTENTION" else "IN_PROGRESS"
    elif session.state in {"STARTING", "RUNNING", "LOGIN_REQUIRED"} and result not in {"SUBMITTED", "FAILED", "MANUAL_REVIEW", "SKIPPED"}:
        result = "IN_PROGRESS"
    audit_actions: set[str] = set()
    audit_rows = []
    scalars = getattr(db, "scalars", None)
    if callable(scalars):
        audit_rows = scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == settings.demo_org_id,
                AuditEvent.target_id == session.id,
            )
        ).all()
        audit_actions = {str(row.action) for row in audit_rows}
    agent_detail = None
    for row in audit_rows:
        action = str(getattr(row, "action", ""))
        if ".newsletter.agent." not in action:
            continue
        row_metadata = getattr(row, "metadata_json", None)
        if isinstance(row_metadata, dict) and row_metadata.get("detail"):
            agent_detail = str(row_metadata["detail"])
    if any(action.endswith("newsletter.agent.control_selected") or action.endswith("newsletter.agent.invoked") for action in audit_actions):
        agent_status = "INVOKED"
    elif any(action.endswith("newsletter.skill.replayed") for action in audit_actions):
        agent_status = "SKILL_REPLAYED"
    elif any(action.endswith("newsletter.agent.no_decision") for action in audit_actions):
        agent_status = "NO_DECISION"
    elif any(action.endswith("newsletter.agent.not_used") for action in audit_actions):
        agent_status = "NOT_USED"
    elif any(action.endswith("newsletter.agent.ready") for action in audit_actions):
        agent_status = "READY"
    else:
        agent_status = "UNKNOWN"
    signup_mode = str(metadata.get("newsletter_signup_mode") or ("ADAPTIVE_LEARNING" if not metadata.get("newsletter_profile_id") else "PROFILE"))
    if signup_mode not in {"PROFILE", "ADAPTIVE_LEARNING", "LIVE_FORM"}:
        signup_mode = "PROFILE"
    return {
        "session": session_response(session),
        "newsletter_id": newsletter_id,
        "profile_id": str(metadata.get("newsletter_profile_id") or ""),
        "subscription_id": subscription_id,
        "result": result,
        "signup_mode": signup_mode,
        "execution_mode": str(metadata.get("execution_mode") or ("AGENT_LEARNING" if signup_mode == "ADAPTIVE_LEARNING" else "LIVE_FORM" if signup_mode == "LIVE_FORM" else "DETERMINISTIC")),
        "agent_status": agent_status,
        "agent_detail": agent_detail,
        "confirmation_required": bool(newsletter.confirmation_required) if newsletter is not None else False,
        "auto_confirm": bool(metadata.get("newsletter_auto_confirm")),
        "confirmation_session_id": metadata.get("newsletter_confirmation_session_id"),
    }


def _active_newsletter_session(db: Session, account_id: str, newsletter_id: str) -> BrowserSession | None:
    """Find a live session for this exact newsletter, never just the account.

    An account can test multiple newsletters over time. Reusing an unrelated
    session made one newsletter appear to be running and prevented its browser
    from opening, especially when the unrelated session was already closed.
    """
    candidates = db.scalars(
        select(BrowserSession)
        .where(
            BrowserSession.organization_id == settings.demo_org_id,
            BrowserSession.account_id == account_id,
            BrowserSession.state.in_({"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}),
        )
        .order_by(BrowserSession.created_at.desc())
    ).all()
    for candidate in candidates:
        metadata = candidate.metadata_json or {}
        if metadata.get("purpose") != "newsletter" or str(metadata.get("newsletter_id") or "") != newsletter_id:
            continue
        if not metadata.get("browser_opened"):
            continue
        if local_browser_is_alive(candidate.profile_id, candidate.id):
            return candidate
        # Keep the reservation during the short observer/Chrome hand-off.
        created_at = candidate.created_at
        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())
            if age_seconds < max(15.0, settings.browser_observe_interval * 5):
                return candidate
    return None


def _newsletter_confirmation_test_response(db: Session, session: BrowserSession) -> dict:
    metadata = session.metadata_json or {}
    subscription_id = str(metadata.get("newsletter_subscription_id") or "")
    subscription = db.get(NewsletterSubscription, subscription_id) if subscription_id else None
    result = str(metadata.get("newsletter_result") or (subscription.state if subscription else "IN_PROGRESS"))
    if session.state in {"STARTING", "RUNNING", "LOGIN_REQUIRED"} and result not in {"WAITING_CONFIRMATION", "CONFIRMED", "FAILED", "MANUAL_REVIEW", "SKIPPED"}:
        result = "IN_PROGRESS"
    return {"session": session_response(session), "newsletter_id": str(metadata.get("newsletter_id") or ""), "profile_id": str(metadata.get("newsletter_profile_id") or ""), "subscription_id": subscription_id, "result": result}


@app.post("/api/v1/newsletters/{newsletter_id}/signup-test", response_model=NewsletterSignupTestResponse, status_code=status.HTTP_201_CREATED)
def newsletter_signup_test(newsletter_id: str, payload: NewsletterSignupTestRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == settings.demo_org_id, GmailAccount.id == payload.account_id, GmailAccount.lifecycle_status != "ARCHIVED"))
    if not account or (not user.is_admin and not user.can_access_group(account.group_id, "OPERATOR")):
        raise HTTPException(status_code=404, detail="Resource not found")
    subscription = db.scalar(select(NewsletterSubscription).where(NewsletterSubscription.organization_id == settings.demo_org_id, NewsletterSubscription.account_id == payload.account_id, NewsletterSubscription.newsletter_id == newsletter_id))
    if subscription and subscription.state == "CONFIRMED":
        subscription.last_checked_at = datetime.now(timezone.utc)
        db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex}", organization_id=settings.demo_org_id, actor_id=user.employee_id, action="newsletter.signup.skipped", target_type="newsletter_subscription", target_id=subscription.id, result="success", metadata_json={"account_id": payload.account_id, "newsletter_id": newsletter_id, "skip_reason": "ALREADY_SUBSCRIBED"}))
        db.commit()
        return {"session": None, "newsletter_id": newsletter_id, "profile_id": payload.profile_id or "", "subscription_id": subscription.id, "result": "SKIPPED_ALREADY_SUBSCRIBED"}
    if subscription and subscription.state in {"SUBMITTED", "WAITING_CONFIRMATION"}:
        active_session = _active_newsletter_session(db, payload.account_id, newsletter_id)
        if active_session and active_session.owner_employee_id != user.employee_id:
            active_session = None
        subscription.last_checked_at = datetime.now(timezone.utc)
        db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex}", organization_id=settings.demo_org_id, actor_id=user.employee_id, action="newsletter.signup.skipped", target_type="newsletter_subscription", target_id=subscription.id, result="success", metadata_json={"account_id": payload.account_id, "newsletter_id": newsletter_id, "skip_reason": "AWAITING_CONFIRMATION", "active_session_id": active_session.id if active_session else None}))
        db.commit()
        return {"session": session_response(active_session) if active_session else None, "newsletter_id": newsletter_id, "profile_id": payload.profile_id or "", "subscription_id": subscription.id, "result": "SKIPPED_ALREADY_IN_PROGRESS"}
    try:
        session = start_newsletter_signup_test(
            db,
            settings.demo_org_id,
            user.employee_id,
            newsletter_id,
            payload.account_id,
            payload.profile_id,
            test_identity=payload.test_identity,
            signup_password=payload.signup_password,
            auto_confirm=payload.auto_confirm,
        )
        return _newsletter_signup_test_response(db, session)
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already" in message or "open" in message else 422, detail=str(exc)) from exc


@app.get("/api/v1/newsletter-signup-tests/{session_id}", response_model=NewsletterSignupTestResponse)
def newsletter_signup_test_detail(session_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    session = get_session(db, settings.demo_org_id, session_id)
    if not session or session.owner_employee_id != user.employee_id or (session.metadata_json or {}).get("purpose") != "newsletter":
        raise HTTPException(status_code=404, detail="Newsletter signup test not found")
    if not user.is_admin and not user.can_access_group(session.account.group_id, "VIEWER"):
        raise HTTPException(status_code=404, detail="Resource not found")
    return _newsletter_signup_test_response(db, session)


@app.post("/api/v1/newsletters/{newsletter_id}/confirm-test", response_model=NewsletterConfirmationTestResponse, status_code=status.HTTP_201_CREATED)
def newsletter_confirmation_test(newsletter_id: str, payload: NewsletterConfirmationTestRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    account = db.scalar(select(GmailAccount).where(GmailAccount.organization_id == settings.demo_org_id, GmailAccount.id == payload.account_id, GmailAccount.lifecycle_status != "ARCHIVED"))
    if not account or (not user.is_admin and not user.can_access_group(account.group_id, "OPERATOR")):
        raise HTTPException(status_code=404, detail="Resource not found")
    try:
        session = start_newsletter_confirmation_test(db, settings.demo_org_id, user.employee_id, newsletter_id, payload.account_id, payload.profile_id, payload.subscription_id)
        return _newsletter_confirmation_test_response(db, session)
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already" in message or "open" in message else 422, detail=str(exc)) from exc


@app.get("/api/v1/newsletter-confirmation-tests/{session_id}", response_model=NewsletterConfirmationTestResponse)
def newsletter_confirmation_test_detail(session_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    session = get_session(db, settings.demo_org_id, session_id)
    if not session or session.owner_employee_id != user.employee_id or (session.metadata_json or {}).get("purpose") != "newsletter_confirm":
        raise HTTPException(status_code=404, detail="Newsletter confirmation test not found")
    if not user.is_admin and not user.can_access_group(session.account.group_id, "VIEWER"):
        raise HTTPException(status_code=404, detail="Resource not found")
    return _newsletter_confirmation_test_response(db, session)


@app.post("/api/v1/newsletter-jobs", response_model=NewsletterJobResponse, status_code=status.HTTP_201_CREATED)
def create_newsletter_job_route(payload: NewsletterJobCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    if not user.is_admin and not user.can_access_group(payload.account_group_id, "OPERATOR"):
        raise HTTPException(status_code=404, detail="Resource not found")
    try:
        return create_newsletter_job(
            db,
            settings.demo_org_id,
            user.employee_id,
            payload.newsletter_id,
            payload.newsletter_group_id,
            payload.account_group_id,
            payload.concurrency_limit,
            payload.retry_policy_json,
            payload.auto_confirm,
        )
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletter-jobs", response_model=NewsletterJobListResponse)
def newsletter_jobs(limit: int = Query(default=50, ge=1, le=200), request: Request = None, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    items, total = list_newsletter_jobs(db, settings.demo_org_id, account_group_ids=_visible_groups(request, db), limit=limit, created_by=user.employee_id)
    return {"items": items, "total": total}


@app.post("/api/v1/newsletter-jobs/account-targets")
def newsletter_job_account_targets(payload: NewsletterAccountTargetsRequest, request: Request, db: Session = Depends(get_db)) -> list[dict]:
    targets = []
    for account_id in payload.account_ids:
        try:
            account = _account_access(request, db, account_id, "OPERATOR")
        except HTTPException as exc:
            if exc.status_code == 404:
                raise HTTPException(status_code=404, detail="One or more selected accounts are unavailable") from exc
            raise
        if account.lifecycle_status == "ARCHIVED" or account.account_type == "EXTERNAL_RECIPIENT":
            raise HTTPException(status_code=404, detail="One or more selected accounts are unavailable")
        targets.append({
            "id": account.id,
            "email": account.email,
            "group_id": account.group_id,
            "group_name": account.group.name if account.group else "Unknown group",
            "login_status": account.login_status,
            "profile_status": account.profile_status,
        })
    return targets


@app.get("/api/v1/newsletter-jobs/{job_id}", response_model=NewsletterJobResponse)
def newsletter_job_detail(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    job = get_newsletter_job(db, settings.demo_org_id, job_id, account_group_ids=_visible_groups(request, db), created_by=user.employee_id)
    if not job:
        raise HTTPException(status_code=404, detail="Newsletter job not found")
    return job


@app.delete("/api/v1/newsletter-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_newsletter_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        delete_newsletter_job_history(db, settings.demo_org_id, user.employee_id, job_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/v1/newsletter-jobs")
def delete_newsletter_jobs_route(payload: NewsletterJobBulkDelete, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    visible_groups = _visible_groups(request, db)
    for job_id in payload.job_ids:
        job = get_newsletter_job(db, settings.demo_org_id, job_id, account_group_ids=visible_groups, created_by=user.employee_id)
        if not job or (not user.is_admin and not user.can_access_group(job["account_group_id"], "OPERATOR")):
            raise HTTPException(status_code=404, detail="One or more newsletter jobs were not found")
    try:
        deleted = delete_newsletter_job_histories(db, settings.demo_org_id, user.employee_id, payload.job_ids)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted_count": deleted, "deleted_ids": payload.job_ids}


def _operable_newsletter_job(job_id: str, request: Request, db: Session):
    user = _current_user(request, db)
    job = get_newsletter_job(db, settings.demo_org_id, job_id, account_group_ids=_visible_groups(request, db), created_by=user.employee_id)
    if not job:
        raise HTTPException(status_code=404, detail="Newsletter job not found")
    if not user.is_admin and not user.can_access_group(job["account_group_id"], "OPERATOR"):
        raise HTTPException(status_code=404, detail="Newsletter job not found")
    return user, job


@app.post("/api/v1/newsletter-jobs/{job_id}/start", response_model=NewsletterJobResponse)
def start_newsletter_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        return start_newsletter_job(db, settings.demo_org_id, user.employee_id, job_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-jobs/{job_id}/dispatch", response_model=NewsletterJobResponse)
def dispatch_newsletter_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        return dispatch_newsletter_job(db, settings.demo_org_id, user.employee_id, job_id)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.get("/api/v1/newsletter-jobs/{job_id}/progress", response_model=NewsletterJobResponse)
def newsletter_job_progress_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _operable_newsletter_job(job_id, request, db)
    job = get_newsletter_job(db, settings.demo_org_id, job_id, account_group_ids=_visible_groups(request, db))
    if not job:
        raise HTTPException(status_code=404, detail="Newsletter job not found")
    return job


@app.post("/api/v1/newsletter-jobs/{job_id}/pause", response_model=NewsletterJobResponse)
def pause_newsletter_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        return update_newsletter_job_state(db, settings.demo_org_id, user.employee_id, job_id, "PAUSED")
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-jobs/{job_id}/resume", response_model=NewsletterJobResponse)
def resume_newsletter_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        return update_newsletter_job_state(db, settings.demo_org_id, user.employee_id, job_id, "RUNNING")
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-jobs/{job_id}/cancel", response_model=NewsletterJobResponse)
def cancel_newsletter_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        return update_newsletter_job_state(db, settings.demo_org_id, user.employee_id, job_id, "CANCELLED")
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-jobs/{job_id}/retry-failed", response_model=NewsletterJobResponse)
def retry_newsletter_job_route(job_id: str, payload: NewsletterJobTaskSelection, request: Request, db: Session = Depends(get_db)) -> dict:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        return retry_newsletter_job(db, settings.demo_org_id, user.employee_id, job_id, payload.task_ids or None)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/newsletter-jobs/{job_id}/skip-selected", response_model=NewsletterJobResponse)
def skip_newsletter_tasks_route(job_id: str, payload: NewsletterJobTaskSelection, request: Request, db: Session = Depends(get_db)) -> dict:
    user, _ = _operable_newsletter_job(job_id, request, db)
    try:
        return skip_newsletter_tasks(db, settings.demo_org_id, user.employee_id, job_id, payload.task_ids)
    except NewsletterValidationError as exc:
        raise HTTPException(status_code=_nl_http_status(str(exc)), detail=str(exc)) from exc


@app.post("/api/v1/maintenance/recover-stale-sessions")
def recover_stale_session_locks(request: Request, db: Session = Depends(get_db)) -> dict[str, int]:
    _admin(request, db)
    return recover_stale_locks(db, settings.demo_org_id)


@app.get("/api/v1/audit-events", response_model=list[AuditEventResponse])
def audit_events(account_id: str | None = None, limit: int = Query(default=25, ge=1, le=100), request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    query = select(AuditEvent).where(AuditEvent.organization_id == settings.demo_org_id).order_by(AuditEvent.created_at.desc()).limit(limit)
    visible = _visible_groups(request, db)
    if visible is not None:
        visible_accounts = select(GmailAccount.id).where(GmailAccount.organization_id == settings.demo_org_id, GmailAccount.group_id.in_(visible))
        query = query.where(or_(AuditEvent.target_id.in_(visible_accounts), AuditEvent.metadata_json["account_id"].as_string().in_(visible_accounts)))
    if account_id:
        # Session events target the session id, so include them when their
        # structured metadata identifies the account being inspected.
        query = query.where(or_(AuditEvent.target_id == account_id, AuditEvent.metadata_json["account_id"].as_string() == account_id))
    return [{"id": item.id, "action": item.action, "target_type": item.target_type, "target_id": item.target_id, "result": item.result, "metadata": item.metadata_json, "created_at": item.created_at} for item in db.scalars(query).all()]


@app.get("/api/v1/sessions", response_model=list[BrowserSessionResponse])
def sessions(account_id: str | None = None, group_id: str | None = None, latest_per_account: bool = False, purpose: str | None = Query(default=None, max_length=40), outcome: str | None = Query(default=None, max_length=32), limit: int = Query(default=25, ge=1, le=100), request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    user = _current_user(request, db)
    visible = _visible_groups(request, db)
    if visible is not None and group_id and group_id not in visible:
        raise HTTPException(status_code=404, detail="Resource not found")
    return list_sessions(db, settings.demo_org_id, account_id, limit, group_id=group_id, latest_per_account=latest_per_account, purpose=purpose, outcome=outcome, visible_group_ids=visible, owner_employee_id=user.employee_id)


@app.delete("/api/v1/sessions/history")
def delete_session_history(request: Request, db: Session = Depends(get_db)) -> dict[str, int]:
    admin = _admin(request, db)
    active_states = {"STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"}
    org_sessions = select(BrowserSession).where(BrowserSession.organization_id == settings.demo_org_id)
    session_rows = org_sessions.subquery()
    active_count = db.scalar(select(func.count()).select_from(session_rows).where(session_rows.c.state.in_(active_states))) or 0
    records = db.scalars(org_sessions.where(BrowserSession.state.notin_(active_states))).all()
    ids = [record.id for record in records]
    if ids:
        # Keep the job and lock ledgers valid while removing only terminal session rows.
        db.execute(update(LoginJobItem).where(LoginJobItem.session_id.in_(ids)).values(session_id=None))
        db.execute(update(JobLock).where(JobLock.session_id.in_(ids)).values(session_id=None))
        db.execute(update(BrowserProfile).where(BrowserProfile.active_session_id.in_(ids)).values(active_session_id=None))
        for record in records:
            db.delete(record)
        db.add(AuditEvent(id=f"audit_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, actor_id=admin.employee_id, action="session.history.deleted", target_type="browser_session_history", target_id=settings.demo_org_id, result="success", metadata_json={"deleted_count": len(ids), "active_preserved_count": active_count}))
        db.commit()
    return {"deleted_count": len(ids), "active_preserved_count": active_count}


@app.get("/api/v1/browser-profiles", response_model=list[BrowserProfileResponse])
def browser_profiles(q: str | None = Query(default=None, max_length=320), status_filter: str | None = Query(default=None, alias="status"), limit: int = Query(default=100, ge=1, le=500), request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    return list_browser_profiles(db, settings.demo_org_id, q, status_filter, limit, visible_group_ids=_visible_groups(request, db))


@app.get("/api/v1/proxies", response_model=list[ProxyResponse])
def proxies(q: str | None = Query(default=None, max_length=320), group_id: str | None = Query(default=None, max_length=64), status_filter: str | None = Query(default=None, alias="status"), request: Request = None, db: Session = Depends(get_db)) -> list[dict]:
    visible = _visible_groups(request, db)
    if visible is not None and group_id and group_id not in visible:
        raise HTTPException(status_code=404, detail="Resource not found")
    return list_proxies(db, settings.demo_org_id, q, status_filter, group_id=group_id, visible_group_ids=visible)


@app.post("/api/v1/proxies/import", response_model=ProxyBulkImportResponse, status_code=status.HTTP_201_CREATED)
def import_proxies(payload: ProxyBulkImportRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    try:
        return import_proxy_inventory(db, settings.demo_org_id, admin.employee_id, payload.lines, payload.group_name)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/proxies/assignments")
def assign_proxy(payload: ProxyAssignmentRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _authorize_proxy_accounts(request, db, payload.account_ids)
    admin = _current_user(request, db)
    try:
        label = payload.label if "label" in payload.model_fields_set else _UNSET
        region = payload.region if "region" in payload.model_fields_set else _UNSET
        return assign_proxy_accounts(db, settings.demo_org_id, admin.employee_id, payload.account_ids, payload.proxy_id, payload.proxy, label, region)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/proxies/{proxy_id}/group-assignments")
def replace_group_proxy_assignments(proxy_id: str, payload: ProxyGroupAssignmentRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Replace one proxy's assignments inside a single authorized account group."""
    _authorize_group(request, payload.group_id, "OPERATOR")
    user = _account_operator(request, db)
    # Legacy account-level proxy records may not have an inventory row yet.
    # The service validates that the endpoint exists in this organization and
    # can rebuild its inventory record from an existing encrypted assignment.
    # Group access remains the authorization boundary for the mutation.
    try:
        label = payload.label if "label" in payload.model_fields_set else _UNSET
        region = payload.region if "region" in payload.model_fields_set else _UNSET
        return replace_proxy_group_assignments(
            db, settings.demo_org_id, user.employee_id, payload.group_id,
            proxy_id, payload.account_ids, label, region,
        )
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Atomic proxy assignment failed proxy_id=%s group_id=%s actor_id=%s", proxy_id, payload.group_id, user.employee_id)
        raise HTTPException(status_code=500, detail="Proxy assignment failed and was rolled back. Check API service logs for the database cause.") from exc


@app.post("/api/v1/proxies/group")
def update_proxy_group_membership(payload: ProxyGroupUpdateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    for proxy_id in payload.proxy_ids:
        _authorize_proxy_record(request, db, proxy_id)
    try:
        return update_proxy_group(db, settings.demo_org_id, admin.employee_id, payload.proxy_ids, payload.group_name)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/proxies/groups")
def get_proxy_groups(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    _account_operator(request, db)
    return list_proxy_groups(db, settings.demo_org_id)


@app.post("/api/v1/proxies/groups", status_code=status.HTTP_201_CREATED)
def create_proxy_group_list(payload: ProxyGroupCreateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _account_operator(request, db)
    try:
        return create_proxy_group(db, settings.demo_org_id, admin.employee_id, payload.name)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/proxies/distribute-group")
def distribute_group_proxies(payload: ProxyGroupDistributionRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _authorize_group(request, payload.group_id, "OPERATOR")
    admin = _current_user(request, db)
    for proxy_id in payload.proxy_ids:
        _authorize_proxy_record(request, db, proxy_id)
    try:
        return distribute_proxies_to_group(db, settings.demo_org_id, admin.employee_id, payload.group_id, payload.proxy_ids, payload.accounts_per_proxy, payload.mode)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/proxies/test", response_model=list[ProxyTestResponse])
def test_proxies(payload: ProxyTestRequest, request: Request, db: Session = Depends(get_db)) -> list[dict]:
    admin = _account_operator(request, db)
    for proxy_id in payload.proxy_ids:
        _authorize_proxy_record(request, db, proxy_id)
    results = []
    for proxy_id in payload.proxy_ids:
        try:
            results.append(test_proxy(db, settings.demo_org_id, admin.employee_id, proxy_id))
        except ImportValidationError as exc:
            results.append({"proxy_id": proxy_id, "status": "NOT_FOUND", "public_ip": None, "provider": None, "country": None, "city": None, "latency_ms": None, "tested_at": datetime.now(timezone.utc), "error": str(exc)})
    return results


@app.patch("/api/v1/proxies/{proxy_id}")
def edit_proxy(proxy_id: str, payload: ProxyUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _authorize_proxy_record(request, db, proxy_id)
    try:
        proxy_value = payload.proxy if "proxy" in payload.model_fields_set else _UNSET
        label = payload.label if "label" in payload.model_fields_set else _UNSET
        region = payload.region if "region" in payload.model_fields_set else _UNSET
        return update_proxy(db, settings.demo_org_id, admin.employee_id, proxy_id, proxy_value, label, region)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.delete("/api/v1/proxies/{proxy_id}/assignments")
def unassign_proxy_accounts(proxy_id: str, payload: ProxyAssignmentRequest | None = None, request: Request = None, db: Session = Depends(get_db)) -> dict:
    if payload and payload.account_ids:
        _authorize_proxy_accounts(request, db, payload.account_ids)
        user = _current_user(request, db)
        if not user.is_admin:
            assigned = set(db.scalars(select(GmailAccount.id).where(GmailAccount.organization_id == settings.demo_org_id, GmailAccount.proxy_id == proxy_id, GmailAccount.id.in_(payload.account_ids))).all())
            if assigned != set(payload.account_ids):
                raise HTTPException(status_code=404, detail="Resource not found")
    else:
        _authorize_proxy_record(request, db, proxy_id)
    admin = _current_user(request, db)
    try:
        count = remove_proxy_assignments(db, settings.demo_org_id, admin.employee_id, proxy_id, payload.account_ids if payload else None)
        return {"proxy_id": proxy_id, "updated_count": count}
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/v1/proxies/{proxy_id}")
def remove_proxy(proxy_id: str, payload: ProxyDeleteRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    admin = _authorize_proxy_record(request, db, proxy_id)
    if payload.confirmation.strip().upper() != "REMOVE":
        raise HTTPException(status_code=422, detail="Type REMOVE to confirm removing this proxy assignment")
    try:
        count = remove_proxy_assignments(db, settings.demo_org_id, admin.employee_id, proxy_id)
        observation = db.get(ProxyInventory, proxy_id)
        if observation:
            db.delete(observation)
            db.commit()
        return {"proxy_id": proxy_id, "updated_count": count}
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/sessions/{session_id}", response_model=BrowserSessionResponse)
def session_detail(session_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _current_user(request, db)
    session = get_session(db, settings.demo_org_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session_response(session)


@app.post("/api/v1/accounts/{account_id}/sessions", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def open_login_session(account_id: str, request: Request, payload: SessionStartRequest | None = None, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(start_login_session(db, settings.demo_org_id, _actor(request, db), account_id, purpose=payload.purpose if payload else "login"))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/contacts-import", response_model=ContactsImportResponse, status_code=status.HTTP_201_CREATED)
def contacts_import(account_id: str, payload: ContactsImportRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        session, contact_file, email_count = start_contacts_import(db, settings.demo_org_id, _actor(request, db), account_id, payload.file_id, payload.emails)
        return {
            "session_id": session.id,
            "account_id": account_id,
            "contact_file_id": contact_file.id if contact_file else None,
            "email_count": email_count,
            "status": session.login_state,
        }
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/labels-markers", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def labels_markers_operation(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(start_labels_markers_operation(db, settings.demo_org_id, _actor(request, db), account_id))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/security", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def security_operation(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(start_security_operation(db, settings.demo_org_id, _actor(request, db), account_id))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/addons", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def addons_operation(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(start_addons_operation(db, settings.demo_org_id, _actor(request, db), account_id))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.get("/api/v1/alerts/topics", response_model=list[AlertTopicResponse])
def google_alert_topics(db: Session = Depends(get_db)) -> list[dict]:
    seed_default_alert_topics(db, settings.demo_org_id)
    return list_alert_topics(db, settings.demo_org_id)


@app.post("/api/v1/accounts/{account_id}/alerts", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def alerts_operation(account_id: str, payload: AlertsOperationRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(start_alerts_operation(db, settings.demo_org_id, _actor(request, db), account_id, payload.topic_count, payload.topics))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.post("/api/v1/groups/{group_id}/sessions/close")
def stop_group_sessions(group_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    if not user.is_admin and not user.can_access_group(group_id, "OPERATOR"):
        raise HTTPException(status_code=404, detail="Resource not found")
    try:
        return close_group_sessions(db, settings.demo_org_id, user.employee_id, group_id, owner_employee_id=user.employee_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/sessions/close-active")
def stop_all_active_sessions(request: Request, db: Session = Depends(get_db)) -> dict:
    user = _current_user(request, db)
    group_ids = None if user.is_admin else set(user.group_access)
    return close_active_sessions(db, settings.demo_org_id, user.employee_id, group_ids, owner_employee_id=user.employee_id)


@app.post("/api/v1/accounts/{account_id}/mail-workflow", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def mail_workflow_operation(account_id: str, payload: MailWorkflowRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        workflow = payload.model_dump()
        return session_response(start_mail_workflow(db, settings.demo_org_id, _actor(request, db), account_id, workflow))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.post("/api/v1/mail-workflow/jobs", response_model=LoginJobResponse, status_code=status.HTTP_201_CREATED)
def create_mail_workflow_job_route(payload: MailWorkflowBulkRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        workflow = payload.model_dump(exclude={"group_id", "account_ids", "concurrency_limit"})
        return create_mail_workflow_job(db, settings.demo_org_id, _actor(request, db), payload.group_id, payload.account_ids, payload.concurrency_limit, workflow)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/mail-workflow/jobs/{job_id}/start", response_model=LoginJobResponse)
def start_mail_workflow_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        update_login_job_state(db, settings.demo_org_id, user.employee_id, job_id, "RUNNING")
        return dispatch_login_job(db, settings.demo_org_id, user.employee_id, job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.get("/api/v1/mail-workflow/jobs/{job_id}/progress", response_model=LoginJobProgressResponse)
def mail_workflow_job_progress_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _operable_login_job(job_id, request, db)
    progress = login_job_progress(db, settings.demo_org_id, job_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Mail workflow job not found")
    return progress


@app.post("/api/v1/mail-workflow/jobs/{job_id}/cancel", response_model=LoginJobResponse)
def cancel_mail_workflow_job_route(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = _operable_login_job(job_id, request, db)
        return update_login_job_state(db, settings.demo_org_id, user.employee_id, job_id, "CANCELLED")
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.get("/api/v1/contact-files", response_model=list[ContactFileResponse])
def contact_files(request: Request, limit: int = Query(default=100, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict]:
    _admin(request, db)
    return list_contact_files(db, settings.demo_org_id, limit)


@app.post("/api/v1/contact-files", response_model=ContactFileResponse, status_code=status.HTTP_201_CREATED)
async def upload_contact_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    try:
        _admin(request, db)
        content = await _read_upload_bounded(file, 10 * 1024 * 1024)
        return contact_file_response(add_contact_file(db, settings.demo_org_id, _actor(request, db), file.filename or "contacts.csv", file.content_type, content))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already" in message else 422, detail=str(exc)) from exc


@app.delete("/api/v1/contact-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_contact_file(file_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    _admin(request, db)
    try:
        delete_contact_file(db, settings.demo_org_id, _actor(request, db), file_id)
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=404 if "not found" in message else 409, detail=str(exc)) from exc


@app.post("/api/v1/sessions/{session_id}/events", response_model=BrowserSessionResponse)
def session_event(session_id: str, payload: SessionEventCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(record_session_event(db, settings.demo_org_id, _actor(request, db), session_id, payload.event_type, payload.detail))
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "closed" in str(exc) else 422, detail=str(exc)) from exc


@app.post("/api/v1/sessions/{session_id}/close", response_model=BrowserSessionResponse)
def end_login_session(session_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(close_session(db, settings.demo_org_id, _actor(request, db), session_id))
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/sessions/{session_id}/observations", response_model=PageObservationResponse)
def session_page_observation(session_id: str, payload: PageObservationCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        signal, action, session = observe_session_page(db, settings.demo_org_id, _actor(request, db), session_id, payload.url, payload.title, payload.text)
        return {"signal": signal, "action": action, "session": session_response(session)}
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "closed" in str(exc) else 404, detail=str(exc)) from exc
