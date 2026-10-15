"""One-time production administrator provisioning command.

Run as the service account after the Alembic schema migration and before the
first production start. Password input is read from the terminal, never argv.
"""
import getpass
import sys
import uuid

from sqlalchemy import select

from .auth import set_employee_password
from .config import get_settings
from .db import SessionLocal
from .models import AccountGroup, Employee, EmployeeAuth, Organization


def main() -> int:
    settings = get_settings()
    if settings.environment.lower() != "production":
        print("This command is intended only for production provisioning.", file=sys.stderr)
        return 2
    email = input("Administrator email: ").strip().lower()
    full_name = input("Administrator full name: ").strip()
    password = getpass.getpass("Administrator password (minimum 14 characters): ")
    if "@" not in email or not full_name or len(password) < 14:
        print("Enter a valid email, a name, and a password with at least 14 characters.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        existing_auth = db.scalar(select(EmployeeAuth.id).where(EmployeeAuth.organization_id == settings.demo_org_id).limit(1))
        if existing_auth:
            print("An administrator credential is already provisioned; refusing to replace it.", file=sys.stderr)
            return 3
        org = db.get(Organization, settings.demo_org_id)
        if org is None:
            org = Organization(id=settings.demo_org_id, name="Northstar Operations")
            db.add(org)
            db.flush()
        employee = db.scalar(select(Employee).where(Employee.organization_id == settings.demo_org_id, Employee.email == email))
        if employee is None:
            employee = Employee(id=f"employee_{uuid.uuid4().hex[:18]}", organization_id=settings.demo_org_id, full_name=full_name, email=email, role="ADMINISTRATOR", status="ACTIVE")
            db.add(employee)
        employee.full_name = full_name
        employee.role = "ADMINISTRATOR"
        employee.status = "ACTIVE"
        set_employee_password(db, employee, password)
        if not db.scalar(select(AccountGroup.id).where(AccountGroup.organization_id == settings.demo_org_id).limit(1)):
            for key, name, description in [
                ("sales-us", "Sales · US", "North America sales account pool"),
                ("support", "Customer Support", "Customer care account pool"),
                ("partnerships", "Partnerships", "Partner operations account pool"),
            ]:
                db.add(AccountGroup(id=f"group_{key}", organization_id=settings.demo_org_id, name=name, description=description, capacity=300))
        db.commit()
    print("Initial administrator provisioned. Remove or rotate any temporary access material before enabling public traffic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
