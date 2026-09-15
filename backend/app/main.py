import uuid
import secrets
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Base, engine, get_db
from .models import AccountGroup, AuditEvent, GmailAccount, Organization
from .schemas import AccountDeleteRequest, AccountListResponse, AccountMetadataUpdate, AccountResponse, AccountStructuredInput, AuditEventResponse, BrowserSessionResponse, BulkImportRequest, BulkPreviewRequest, BulkPreviewResponse, CredentialRevisionResponse, CredentialUpdate, GroupCreate, GroupMoveRequest, GroupResponse, ImportSummary, LoginJobCreate, LoginJobProgressResponse, LoginJobResponse, OperationDraftCreate, OperationDraftResponse, PageObservationCreate, PageObservationResponse, SessionEventCreate, SessionStartRequest
from .service import ImportValidationError, account_response, add_account, archive_account, cleanup_unsaved_invalid_profiles, close_session, create_login_job, create_operation_draft, credential_revisions, dispatch_login_job, get_account, get_login_job, get_session, import_accounts, list_accounts, list_login_jobs, list_operation_drafts, list_sessions, login_job_progress, move_account_group, observe_session_page, preview_import, record_session_event, recover_stale_locks, resume_login_job, session_response, start_login_session, update_account_metadata, update_credentials, update_login_job_state
from .worker.worker import start_login_worker, stop_login_worker

settings = get_settings()

logger = logging.getLogger("gmail_ops")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)


def startup() -> None:
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
        cleanup_unsaved_invalid_profiles(db, settings.demo_org_id)
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
    yield
    stop_login_worker()


app = FastAPI(title=settings.app_name, version="0.1.0", description="Phase 1 account onboarding; no Gmail login automation", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    """Require a bearer token for every non-health API request outside development."""
    if request.method != "OPTIONS" and request.url.path.startswith("/api/v1/") and request.url.path != "/api/v1/health":
        if settings.environment.lower() != "development" or settings.admin_api_token:
            authorization = request.headers.get("authorization", "")
            scheme, _, supplied = authorization.partition(" ")
            if not settings.admin_api_token:
                return JSONResponse(status_code=503, content={"detail": "API authentication is not configured"})
            if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, settings.admin_api_token):
                return JSONResponse(status_code=401, headers={"WWW-Authenticate": "Bearer"}, content={"detail": "Authentication required"})
    return await call_next(request)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "1", "boundary": "ready_for_login"}


@app.get("/api/v1/groups", response_model=list[GroupResponse])
def groups(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(AccountGroup, func.count(GmailAccount.id)).outerjoin(GmailAccount, (GmailAccount.group_id == AccountGroup.id) & (GmailAccount.lifecycle_status != "ARCHIVED")).where(AccountGroup.organization_id == settings.demo_org_id).group_by(AccountGroup.id).order_by(AccountGroup.name)).all()
    return [{"id": group.id, "name": group.name, "description": group.description, "capacity": group.capacity, "account_count": count, "ready_count": count, "issue_count": 0, "status": group.status} for group, count in rows]


@app.post("/api/v1/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, db: Session = Depends(get_db)) -> dict:
    group = AccountGroup(id=f"group_{payload.name.lower().replace(' ', '-')[:30]}_{uuid.uuid4().hex[:6]}", organization_id=settings.demo_org_id, name=payload.name, description=payload.description, capacity=payload.capacity)
    db.add(group)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A group with this name already exists") from exc
    return {"id": group.id, "name": group.name, "description": group.description, "capacity": group.capacity, "account_count": 0, "ready_count": 0, "issue_count": 0, "status": group.status}


@app.get("/api/v1/accounts", response_model=AccountListResponse)
def accounts(q: str | None = Query(default=None, max_length=320), group_id: str | None = None, status_filter: str | None = Query(default=None, alias="status"), page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
    return list_accounts(db, settings.demo_org_id, q, group_id, status_filter, page, page_size)


@app.get("/api/v1/accounts/{account_id}", response_model=AccountResponse)
def account_detail(account_id: str, db: Session = Depends(get_db)) -> dict:
    account = get_account(db, settings.demo_org_id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account_response(account)


@app.post("/api/v1/accounts/single", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountStructuredInput, db: Session = Depends(get_db)) -> dict:
    try:
        return account_response(add_account(db, settings.demo_org_id, "admin_demo", payload))
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/v1/accounts/{account_id}", response_model=AccountResponse)
def edit_account_metadata(account_id: str, payload: AccountMetadataUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        return update_account_metadata(db, settings.demo_org_id, "admin_demo", account_id, payload.proxy_label, payload.proxy_region)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc)) from exc


@app.post("/api/v1/accounts/{account_id}/group-move", response_model=AccountResponse)
def move_account(account_id: str, payload: GroupMoveRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return move_account_group(db, settings.demo_org_id, "admin_demo", account_id, payload.destination_group_id)
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


@app.post("/api/v1/accounts/bulk/preview", response_model=BulkPreviewResponse)
def bulk_preview(payload: BulkPreviewRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return preview_import(db, settings.demo_org_id, payload.group_id, payload.raw_lines)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/accounts/bulk/import", response_model=ImportSummary, status_code=status.HTTP_201_CREATED)
def bulk_import(payload: BulkImportRequest, db: Session = Depends(get_db)) -> dict:
    try:
        imported, duplicates, invalid, ids = import_accounts(db, settings.demo_org_id, "admin_demo", payload.group_id, payload.raw_lines, payload.idempotency_key)
        return {"imported": imported, "duplicates": duplicates, "invalid": invalid, "account_ids": ids}
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/v1/accounts/{account_id}/credentials", response_model=AccountResponse)
def replace_account_credentials(account_id: str, payload: CredentialUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        return update_credentials(db, settings.demo_org_id, "admin_demo", account_id, payload.password, payload.recovery, payload.two_fa)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc)) from exc


@app.get("/api/v1/accounts/{account_id}/credential-revisions", response_model=list[CredentialRevisionResponse])
def account_credential_revisions(account_id: str, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return credential_revisions(db, settings.demo_org_id, account_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs", response_model=LoginJobResponse, status_code=status.HTTP_201_CREATED)
def create_bulk_login_job(payload: LoginJobCreate, db: Session = Depends(get_db)) -> dict:
    try:
        return create_login_job(db, settings.demo_org_id, "admin_demo", payload.group_id, payload.account_ids, payload.concurrency_limit)
    except ImportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/login-jobs", response_model=list[LoginJobResponse])
def login_jobs(limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict]:
    return list_login_jobs(db, settings.demo_org_id, limit)


@app.get("/api/v1/login-jobs/{job_id}", response_model=LoginJobResponse)
def login_job_detail(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = get_login_job(db, settings.demo_org_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Login batch not found")
    return job


@app.post("/api/v1/login-jobs/{job_id}/start", response_model=LoginJobResponse)
def start_bulk_login_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        update_login_job_state(db, settings.demo_org_id, "admin_demo", job_id, "RUNNING")
        return dispatch_login_job(db, settings.demo_org_id, "admin_demo", job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/pause", response_model=LoginJobResponse)
def pause_bulk_login_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return update_login_job_state(db, settings.demo_org_id, "admin_demo", job_id, "PAUSED")
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/dispatch", response_model=LoginJobResponse)
def dispatch_bulk_login_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return dispatch_login_job(db, settings.demo_org_id, "admin_demo", job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "start" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.get("/api/v1/login-jobs/{job_id}/progress", response_model=LoginJobProgressResponse)
def login_job_progress_endpoint(job_id: str, db: Session = Depends(get_db)) -> dict:
    progress = login_job_progress(db, settings.demo_org_id, job_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Login batch not found")
    return progress


@app.post("/api/v1/login-jobs/{job_id}/dispatch-next", response_model=LoginJobResponse)
def dispatch_next_login_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return dispatch_login_job(db, settings.demo_org_id, "admin_demo", job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "start" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/resume", response_model=LoginJobResponse)
def resume_bulk_login_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return resume_login_job(db, settings.demo_org_id, "admin_demo", job_id)
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "resume" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/login-jobs/{job_id}/cancel", response_model=LoginJobResponse)
def cancel_bulk_login_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return update_login_job_state(db, settings.demo_org_id, "admin_demo", job_id, "CANCELLED")
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "cannot move" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/v1/maintenance/recover-stale-sessions")
def recover_stale_session_locks(db: Session = Depends(get_db)) -> dict[str, int]:
    return recover_stale_locks(db, settings.demo_org_id)


@app.get("/api/v1/audit-events", response_model=list[AuditEventResponse])
def audit_events(account_id: str | None = None, limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict]:
    query = select(AuditEvent).where(AuditEvent.organization_id == settings.demo_org_id).order_by(AuditEvent.created_at.desc()).limit(limit)
    if account_id:
        query = query.where(AuditEvent.target_id == account_id)
    return [{"id": item.id, "action": item.action, "target_type": item.target_type, "target_id": item.target_id, "result": item.result, "metadata": item.metadata_json, "created_at": item.created_at} for item in db.scalars(query).all()]


@app.get("/api/v1/sessions", response_model=list[BrowserSessionResponse])
def sessions(account_id: str | None = None, limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict]:
    return list_sessions(db, settings.demo_org_id, account_id, limit)


@app.get("/api/v1/sessions/{session_id}", response_model=BrowserSessionResponse)
def session_detail(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = get_session(db, settings.demo_org_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session_response(session)


@app.post("/api/v1/accounts/{account_id}/sessions", response_model=BrowserSessionResponse, status_code=status.HTTP_201_CREATED)
def open_login_session(account_id: str, payload: SessionStartRequest | None = None, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(start_login_session(db, settings.demo_org_id, "admin_demo", account_id, purpose=payload.purpose if payload else "login"))
    except ImportValidationError as exc:
        message = str(exc).lower()
        raise HTTPException(status_code=409 if "already has" in message or "archived" in message else 422, detail=str(exc)) from exc


@app.post("/api/v1/sessions/{session_id}/events", response_model=BrowserSessionResponse)
def session_event(session_id: str, payload: SessionEventCreate, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(record_session_event(db, settings.demo_org_id, "admin_demo", session_id, payload.event_type, payload.detail))
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "closed" in str(exc) else 422, detail=str(exc)) from exc


@app.post("/api/v1/sessions/{session_id}/close", response_model=BrowserSessionResponse)
def end_login_session(session_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(close_session(db, settings.demo_org_id, "admin_demo", session_id))
    except ImportValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/sessions/{session_id}/observations", response_model=PageObservationResponse)
def session_page_observation(session_id: str, payload: PageObservationCreate, db: Session = Depends(get_db)) -> dict:
    try:
        signal, action, session = observe_session_page(db, settings.demo_org_id, "admin_demo", session_id, payload.url, payload.title, payload.text)
        return {"signal": signal, "action": action, "session": session_response(session)}
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "closed" in str(exc) else 404, detail=str(exc)) from exc
