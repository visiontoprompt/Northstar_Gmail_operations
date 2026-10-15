"""Local workspace authentication and authorization primitives.

This is intentionally separate from Gmail credentials. Employee passwords are
hashed, employee sessions are opaque HttpOnly-cookie sessions, and account
group access is resolved from the audited employee_group_access table.
"""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .models import Employee, EmployeeAuth, EmployeeGroupAccess, EmployeeSession

SESSION_COOKIE = "northstar_session"
ROLE_ADMIN = "ADMINISTRATOR"
ACCESS_RANK = {"VIEWER": 1, "OPERATOR": 2, "MANAGER": 3}


@dataclass(frozen=True)
class CurrentUser:
    employee_id: str
    organization_id: str
    full_name: str
    email: str
    role: str
    group_access: dict[str, str]
    session_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def can_access_group(self, group_id: str, minimum: str = "VIEWER") -> bool:
        if self.role == "AUDITOR" and minimum != "VIEWER":
            return False
        return self.is_admin or ACCESS_RANK.get(self.group_access.get(group_id, ""), 0) >= ACCESS_RANK[minimum]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _user_from_employee(employee: Employee, session_id: str | None = None) -> CurrentUser:
    return CurrentUser(
        employee_id=employee.id,
        organization_id=employee.organization_id,
        full_name=employee.full_name,
        email=employee.email,
        role=employee.role,
        group_access={item.group_id: item.access_level for item in employee.group_access},
        session_id=session_id,
    )


def employee_auth_response(user: CurrentUser) -> dict:
    return {
        "employee": {"id": user.employee_id, "full_name": user.full_name, "email": user.email, "role": user.role},
        "permissions": {
            "is_admin": user.is_admin,
            "group_ids": list(user.group_access),
            "can_manage_workspace": user.is_admin,
            "can_operate_accounts": user.is_admin or (user.role != "AUDITOR" and any(ACCESS_RANK.get(level, 0) >= ACCESS_RANK["OPERATOR"] for level in user.group_access.values())),
        },
    }


def authenticate_request(request: Request, db: Session) -> CurrentUser | None:
    """Resolve the signed-in employee; dev fallback is opt-in and documented."""
    settings = get_settings()
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    if settings.admin_api_token and scheme.lower() == "bearer" and hmac.compare_digest(supplied, settings.admin_api_token):
        return CurrentUser("admin_token", settings.demo_org_id, "API Administrator", settings.bootstrap_admin_email, ROLE_ADMIN, {})
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = db.scalar(
            select(EmployeeSession)
            .options(selectinload(EmployeeSession.employee).selectinload(Employee.group_access))
            .where(EmployeeSession.token_hash == hashlib.sha256(token.encode()).hexdigest(), EmployeeSession.revoked_at.is_(None), EmployeeSession.expires_at > _now())
        )
        if session and session.employee.status == "ACTIVE":
            session.last_seen_at = _now()
            db.commit()
            return _user_from_employee(session.employee, session.id)
    if not settings.auth_required and settings.environment.lower() == "development":
        return CurrentUser("admin_demo", settings.demo_org_id, "Alex Morgan", settings.bootstrap_admin_email, ROLE_ADMIN, {})
    return None


def require_user(request: Request, db: Session) -> CurrentUser:
    user = authenticate_request(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to the workspace")
    return user


def require_admin(user: CurrentUser) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return user


def authorize_group(user: CurrentUser, group_id: str, minimum: str = "VIEWER") -> None:
    if not user.can_access_group(group_id, minimum):
        raise HTTPException(status_code=404, detail="Resource not found")


def authorize_employee_group(db: Session, user: CurrentUser, employee_id: str, group_id: str, minimum: str = "VIEWER") -> None:
    if user.is_admin:
        return
    if employee_id != user.employee_id:
        raise HTTPException(status_code=403, detail="You cannot change another employee's access")
    authorize_group(user, group_id, minimum)


def create_employee_session(db: Session, employee: Employee, password: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add(EmployeeSession(id=f"empsess_{secrets.token_hex(12)}", organization_id=employee.organization_id, employee_id=employee.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=_now() + timedelta(hours=get_settings().session_ttl_hours)))
    return token


def set_employee_password(db: Session, employee: Employee, password: str) -> None:
    auth = employee.auth or EmployeeAuth(id=f"empauth_{secrets.token_hex(12)}", organization_id=employee.organization_id, employee_id=employee.id)
    auth.password_hash = hash_password(password)
    auth.failed_attempts = 0
    auth.locked_until = None
    db.add(auth)
