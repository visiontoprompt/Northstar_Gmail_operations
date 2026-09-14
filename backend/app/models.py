from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    groups: Mapped[list["AccountGroup"]] = relationship(back_populates="organization")


class AccountGroup(Base):
    __tablename__ = "account_groups"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_group_org_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    capacity: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    organization: Mapped[Organization] = relationship(back_populates="groups")
    accounts: Mapped[list["GmailAccount"]] = relationship(back_populates="group")


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "normalized_email", name="uq_account_org_email"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("account_groups.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), default="GMAIL", nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(48), default="SETUP_REQUIRED", nullable=False, index=True)
    readiness_status: Mapped[str] = mapped_column(String(48), default="READY_FOR_LOGIN", nullable=False, index=True)
    login_status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED", nullable=False)
    gmail_status: Mapped[str] = mapped_column(String(32), default="NOT_CHECKED", nullable=False)
    recovery_hint: Mapped[str] = mapped_column(String(320), default="Not provided", nullable=False)
    two_fa_configured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    proxy_id: Mapped[str | None] = mapped_column(String(96))
    proxy_label: Mapped[str | None] = mapped_column(String(160))
    proxy_region: Mapped[str | None] = mapped_column(String(80))
    proxy_status: Mapped[str] = mapped_column(String(32), default="UNASSIGNED", nullable=False)
    profile_id: Mapped[str | None] = mapped_column(String(96))
    profile_storage_ref: Mapped[str | None] = mapped_column(String(500))
    profile_status: Mapped[str] = mapped_column(String(32), default="RESERVED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    group: Mapped[AccountGroup] = relationship(back_populates="accounts")
    secret: Mapped["AccountSecret"] = relationship(back_populates="account", uselist=False, cascade="all, delete-orphan")


class AccountSecret(Base):
    __tablename__ = "account_secrets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    account: Mapped[GmailAccount] = relationship(back_populates="secret")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(96), default="admin_demo", nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(48), nullable=False)
    target_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class ImportOperation(Base):
    __tablename__ = "import_operations"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_import_org_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    imported: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False)
    invalid: Mapped[int] = mapped_column(Integer, nullable=False)
    account_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
