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
    browser_profile: Mapped["BrowserProfile"] = relationship(back_populates="account", uselist=False, cascade="all, delete-orphan")
    sessions: Mapped[list["BrowserSession"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    credential_revisions: Mapped[list["CredentialRevision"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    operation_drafts: Mapped[list["OperationDraft"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class BrowserProfile(Base):
    __tablename__ = "browser_profiles"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="RESERVED", nullable=False)
    lock_state: Mapped[str] = mapped_column(String(32), default="UNLOCKED", nullable=False)
    active_session_id: Mapped[str | None] = mapped_column(String(96))
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    account: Mapped[GmailAccount] = relationship(back_populates="browser_profile")
    sessions: Mapped[list["BrowserSession"]] = relationship(back_populates="profile")


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("browser_profiles.id"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default="STARTING", nullable=False, index=True)
    login_state: Mapped[str] = mapped_column(String(40), default="REQUIRED", nullable=False)
    runtime_type: Mapped[str] = mapped_column(String(32), default="LOCAL_HANDOFF", nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    account: Mapped[GmailAccount] = relationship(back_populates="sessions")
    profile: Mapped[BrowserProfile] = relationship(back_populates="sessions")


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


class CredentialRevision(Base):
    __tablename__ = "credential_revisions"
    __table_args__ = (UniqueConstraint("account_id", "version", name="uq_credential_revision_account_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_hint: Mapped[str] = mapped_column(String(320), nullable=False)
    two_fa_configured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    created_by: Mapped[str] = mapped_column(String(96), default="admin_demo", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    account: Mapped[GmailAccount] = relationship(back_populates="credential_revisions")


class OperationDraft(Base):
    """Versioned, non-secret plan for a future account operation."""

    __tablename__ = "operation_drafts"
    __table_args__ = (UniqueConstraint("account_id", "operation_type", "version", name="uq_operation_draft_account_type_version"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False, index=True)
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(96), default="admin_demo", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    account: Mapped[GmailAccount] = relationship(back_populates="operation_drafts")


class LoginJob(Base):
    __tablename__ = "login_jobs"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("account_groups.id"), index=True)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", nullable=False, index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queued_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    running_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attention_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(96), default="admin_demo", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    items: Mapped[list["LoginJobItem"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class LoginJobItem(Base):
    __tablename__ = "login_job_items"
    __table_args__ = (UniqueConstraint("job_id", "account_id", name="uq_login_job_account"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("login_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("browser_sessions.id"), index=True)
    state: Mapped[str] = mapped_column(String(24), default="QUEUED", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
    worker_id: Mapped[str | None] = mapped_column(String(96), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_key: Mapped[str | None] = mapped_column(String(160))
    paused_reason: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    job: Mapped[LoginJob] = relationship(back_populates="items")
    account: Mapped[GmailAccount] = relationship()


class JobLock(Base):
    """Durable database lock for an account+profile lease held by a worker."""

    __tablename__ = "job_locks"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("browser_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("browser_sessions.id"), index=True)
    worker_id: Mapped[str] = mapped_column(String(96), nullable=False)
    lock_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(120))
    account: Mapped[GmailAccount] = relationship()
