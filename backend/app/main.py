import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Base, engine, get_db
from .models import AccountGroup, AuditEvent, GmailAccount, Organization
from .schemas import AccountListResponse, AccountResponse, AccountStructuredInput, AuditEventResponse, BrowserSessionResponse, BulkImportRequest, BulkPreviewRequest, BulkPreviewResponse, GroupCreate, GroupResponse, ImportSummary, PageObservationCreate, PageObservationResponse, SessionEventCreate
from .service import ImportValidationError, account_response, add_account, close_session, get_account, get_session, import_accounts, list_accounts, list_sessions, observe_session_page, preview_import, record_session_event, session_response, start_login_session

settings = get_settings()


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
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", description="Phase 1 account onboarding; no Gmail login automation", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "1", "boundary": "ready_for_login"}


@app.get("/api/v1/groups", response_model=list[GroupResponse])
def groups(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(AccountGroup, func.count(GmailAccount.id)).outerjoin(GmailAccount).where(AccountGroup.organization_id == settings.demo_org_id).group_by(AccountGroup.id).order_by(AccountGroup.name)).all()
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
def open_login_session(account_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return session_response(start_login_session(db, settings.demo_org_id, "admin_demo", account_id))
    except ImportValidationError as exc:
        raise HTTPException(status_code=409 if "already has" in str(exc) else 422, detail=str(exc)) from exc


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
