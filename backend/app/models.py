from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    employees: Mapped[list["Employee"]] = relationship(back_populates="organization")
    workspace_settings: Mapped["WorkspaceSettings | None"] = relationship(back_populates="organization", uselist=False, cascade="all, delete-orphan")


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_employee_org_email"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(48), default="ACCOUNT_OPERATOR", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    organization: Mapped[Organization] = relationship(back_populates="employees")
    group_access: Mapped[list["EmployeeGroupAccess"]] = relationship(back_populates="employee", cascade="all, delete-orphan", lazy="selectin")
    auth: Mapped["EmployeeAuth | None"] = relationship(back_populates="employee", uselist=False, cascade="all, delete-orphan")


class EmployeeAuth(Base):
    __tablename__ = "employee_auth"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    employee: Mapped[Employee] = relationship(back_populates="auth")


class EmployeeSession(Base):
    __tablename__ = "employee_sessions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    employee: Mapped[Employee] = relationship()


class EmployeeGroupAccess(Base):
    __tablename__ = "employee_group_access"
    __table_args__ = (UniqueConstraint("employee_id", "group_id", name="uq_employee_group_access"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("account_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    access_level: Mapped[str] = mapped_column(String(24), default="OPERATOR", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    employee: Mapped[Employee] = relationship(back_populates="group_access")
    group: Mapped["AccountGroup"] = relationship(back_populates="employee_access")


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_workspace_settings_org"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    default_group_capacity: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    default_login_concurrency: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    max_concurrent_operations: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    max_active_profiles: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    observer_interval_ms: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    session_idle_timeout_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    auto_close_completed_sessions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retain_audit_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    newsletter_agent_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    newsletter_agent_provider: Mapped[str] = mapped_column(String(32), default="openrouter", nullable=False)
    newsletter_agent_model: Mapped[str] = mapped_column(String(160), default="openrouter/free", nullable=False)
    newsletter_agent_temperature: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    newsletter_agent_max_steps: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    newsletter_agent_timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    newsletter_agent_shadow_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    newsletter_agent_live_execution_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    newsletter_agent_learning_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    newsletter_agent_primary_model: Mapped[str] = mapped_column(String(160), default="inclusionai/ling-3.0-flash-vl:free", nullable=False)
    newsletter_agent_secondary_model: Mapped[str] = mapped_column(String(160), default="nex-agi/Nex-N2.5-Mini:free", nullable=False)
    newsletter_agent_free_router_model: Mapped[str] = mapped_column(String(160), default="openrouter/free", nullable=False)
    newsletter_agent_max_ai_calls: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    newsletter_agent_min_confidence: Mapped[float] = mapped_column(Float, default=0.70, nullable=False)
    newsletter_agent_vision_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    newsletter_agent_vision_max_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    newsletter_agent_skill_replay_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    newsletter_agent_deterministic_resolver_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    newsletter_agent_automatic_relearning_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    newsletter_agent_chain: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    openrouter_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    openai_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    two_captcha_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    two_captcha_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ovh_endpoint: Mapped[str] = mapped_column(String(16), default="ovh-eu", nullable=False)
    ovh_credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    organization: Mapped[Organization] = relationship(back_populates="workspace_settings")


class WorkspaceNotificationSettings(Base):
    """Workspace-owned notification transport configuration.

    Provider secrets are stored encrypted and are intentionally never exposed
    through API response models.
    """

    __tablename__ = "workspace_notification_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_workspace_notification_settings_org"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    telegram_bot_token_encrypted: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(160))
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_provider: Mapped[str] = mapped_column(String(16), default="NONE", nullable=False)
    email_recipient_email: Mapped[str | None] = mapped_column(String(320))
    email_from_address: Mapped[str | None] = mapped_column(String(320))
    resend_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    smtp_host: Mapped[str | None] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_username: Mapped[str | None] = mapped_column(String(320))
    smtp_password_encrypted: Mapped[str | None] = mapped_column(Text)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_employee_logins: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_group_reports: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_account_status: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_operation_failures: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class EmployeePasswordResetToken(Base):
    __tablename__ = "employee_password_reset_tokens"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class GoogleAlertTopic(Base):
    __tablename__ = "google_alert_topics"
    __table_args__ = (UniqueConstraint("organization_id", "topic", name="uq_alert_topic_org_topic"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="General", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


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
    employee_access: Mapped[list[EmployeeGroupAccess]] = relationship(back_populates="group", cascade="all, delete-orphan")
    newsletter_jobs: Mapped[list["NewsletterJob"]] = relationship(back_populates="account_group")


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
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
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
    credential_change_plans: Mapped[list["CredentialChangePlan"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    operation_drafts: Mapped[list["OperationDraft"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    newsletter_subscriptions: Mapped[list["NewsletterSubscription"]] = relationship(back_populates="account")


class ProxyInventory(Base):
    """Safe, organization-scoped inventory for a deterministic proxy id.

    The connection itself remains encrypted so an unassigned proxy can be
    reassigned without depending on an account that currently uses it.
    """

    __tablename__ = "proxy_inventory"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(160))
    proxy_group: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(80))
    provider: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(120))
    public_ip: Mapped[str | None] = mapped_column(String(64))
    test_status: Mapped[str] = mapped_column(String(32), default="NOT_TESTED", nullable=False)
    test_error: Mapped[str | None] = mapped_column(String(240))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ProxyGroup(Base):
    """A named proxy list, including lists that do not have endpoints yet."""

    __tablename__ = "proxy_groups"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_proxy_group_org_name"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OvhProxyGroupLink(Base):
    """A proxy group mapped to an OVH server or Public Cloud instance."""

    __tablename__ = "ovh_proxy_group_links"
    __table_args__ = (UniqueConstraint("organization_id", "proxy_group", "resource_type", "service_name", "instance_id", name="uq_ovh_proxy_group_resource"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    proxy_group: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    service_name: Mapped[str] = mapped_column(String(128), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class GoDaddyAccount(Base):
    __tablename__ = "godaddy_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "label", name="uq_godaddy_account_label"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), default="production", nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


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
    authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class ContactFile(Base):
    """Reusable, organization-scoped contact import asset."""

    __tablename__ = "contact_files"
    __table_args__ = (UniqueConstraint("organization_id", "checksum", name="uq_contact_file_org_checksum"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False, default="text/csv")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="READY", index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(96), default="admin_demo", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class LoginJob(Base):
    __tablename__ = "login_jobs"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("account_groups.id"), index=True)
    operation_type: Mapped[str] = mapped_column(String(32), default="login", nullable=False, index=True)
    operation_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
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


class CredentialChangePlan(Base):
    """Audit-safe, server-backed plan for rotating an account's credentials.

    Only the recovery rotation pool is stored here, encrypted at rest. The
    actual new password / recovery / 2FA values are never stored in the plan:
    they are encrypted into a new CredentialRevision only when the operator
    confirms the Google side of the change.
    """

    __tablename__ = "credential_change_plans"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False, index=True)
    password_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recovery_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    two_fa_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recovery_pool_encrypted: Mapped[str | None] = mapped_column(Text)
    recovery_hint: Mapped[str | None] = mapped_column(String(320))
    recovery_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(96))
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(96), default="admin_demo", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account: Mapped[GmailAccount] = relationship(back_populates="credential_change_plans")


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


class NewsletterGroup(Base):
    """Newsletter organization bucket. Deliberately NOT an AccountGroup.

    Newsletter groups organize newsletters only; they never authorize access
    to Gmail account groups, so they are never passed to authorize_group().
    """

    __tablename__ = "newsletter_groups"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_newsletter_group_org_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    newsletters: Mapped[list["Newsletter"]] = relationship(back_populates="group")


class Newsletter(Base):
    """A newsletter source in the registry.

    website_url/signup_url are deliberately not globally unique: one site may
    legitimately host several newsletters. Operational status and discovery
    status are kept as distinct columns.
    """

    __tablename__ = "newsletters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("newsletter_groups.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(500))
    signup_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False, index=True)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_confirmation_domains: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    sender_patterns: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    subject_patterns: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    discovery_status: Mapped[str] = mapped_column(String(32), default="NOT_DISCOVERED", nullable=False, index=True)
    last_discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    group: Mapped[NewsletterGroup] = relationship(back_populates="newsletters")
    profiles: Mapped[list["NewsletterAutomationProfile"]] = relationship(back_populates="newsletter")
    subscriptions: Mapped[list["NewsletterSubscription"]] = relationship(back_populates="newsletter")


class NewsletterAutomationProfile(Base):
    """Immutable, versioned automation configuration for one newsletter.

    A new discovery always creates a new version; existing versions are never
    silently overwritten. Exactly one profile may become ACTIVE at a time; the
    previous ACTIVE version transitions to SUPERSEDED for audit/rollback.
    """

    __tablename__ = "newsletter_automation_profiles"
    __table_args__ = (UniqueConstraint("newsletter_id", "version", name="uq_newsletter_profile_newsletter_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    newsletter_id: Mapped[str] = mapped_column(ForeignKey("newsletters.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DISCOVERED", nullable=False, index=True)
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    newsletter: Mapped[Newsletter] = relationship(back_populates="profiles")


class NewsletterAutomationSkill(Base):
    """Redacted, versioned browser action skill learned for one website."""

    __tablename__ = "newsletter_automation_skills"
    __table_args__ = (UniqueConstraint("newsletter_id", "version", name="uq_newsletter_skill_newsletter_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    newsletter_id: Mapped[str] = mapped_column(ForeignKey("newsletters.id"), nullable=False, index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("newsletter_automation_profiles.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False, index=True)
    skill_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    validation_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class NewsletterSubscription(Base):
    """Idempotent registry of a Gmail account's relationship to a newsletter.

    The (organization_id, account_id, newsletter_id) unique constraint is the
    persistence-level idempotency gate against duplicate signups.
    """

    __tablename__ = "newsletter_subscriptions"
    __table_args__ = (UniqueConstraint("organization_id", "account_id", "newsletter_id", name="uq_newsletter_subscription_org_account_newsletter"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id"), nullable=False, index=True)
    newsletter_id: Mapped[str] = mapped_column(ForeignKey("newsletters.id"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    signup_password_encrypted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    account: Mapped[GmailAccount] = relationship(back_populates="newsletter_subscriptions")
    newsletter: Mapped[Newsletter] = relationship(back_populates="subscriptions")


class NewsletterAccountIdentity(Base):
    """Encrypted, operator-approved non-secret signup details per account."""

    __tablename__ = "newsletter_account_identities"
    __table_args__ = (UniqueConstraint("organization_id", "account_id", name="uq_newsletter_identity_org_account"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    account: Mapped[GmailAccount] = relationship()


class NewsletterJob(Base):
    """Definition and lifecycle record for a bounded bulk newsletter run.

    The job scope must be meaningful: it requires at least one of newsletter_id
    or newsletter_group_id, and always targets a Gmail account group.
    """

    __tablename__ = "newsletter_jobs"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    newsletter_id: Mapped[str | None] = mapped_column(ForeignKey("newsletters.id"), index=True)
    newsletter_group_id: Mapped[str | None] = mapped_column(ForeignKey("newsletter_groups.id"), index=True)
    account_group_id: Mapped[str] = mapped_column(ForeignKey("account_groups.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False, index=True)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    retry_policy_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    auto_confirm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(96), default="admin_demo", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    account_group: Mapped[AccountGroup] = relationship(back_populates="newsletter_jobs")
    tasks: Mapped[list["NewsletterTask"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class NewsletterTask(Base):
    """One account+newsletter unit of work inside a NewsletterJob.

    Task state is durable so queued, active, retrying, and manual-review work
    remains auditable across worker restarts.
    """

    __tablename__ = "newsletter_tasks"
    __table_args__ = (UniqueConstraint("job_id", "account_id", "newsletter_id", name="uq_newsletter_task_job_account_newsletter"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("newsletter_jobs.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("gmail_accounts.id"), nullable=False, index=True)
    newsletter_id: Mapped[str] = mapped_column(ForeignKey("newsletters.id"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    profile_version: Mapped[int | None] = mapped_column(Integer)
    worker_id: Mapped[str | None] = mapped_column(String(96), index=True)
    browser_session_id: Mapped[str | None] = mapped_column(String(96))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_key: Mapped[str | None] = mapped_column(String(160))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    job: Mapped[NewsletterJob] = relationship(back_populates="tasks")
    events: Mapped[list["NewsletterTaskEvent"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class NewsletterTaskEvent(Base):
    """Bounded, secret-safe operational event on a newsletter task.

    detail must never carry passwords, recovery values, 2FA seeds/OTPs,
    cookies, proxy passwords, email bodies, raw page text, HTML dumps, or
    authentication tokens.
    """

    __tablename__ = "newsletter_task_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("newsletter_tasks.id"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    task: Mapped[NewsletterTask] = relationship(back_populates="events")
