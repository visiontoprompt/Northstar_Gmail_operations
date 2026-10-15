from datetime import datetime
from enum import StrEnum
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .newsletter_identity import clean_identity_values


class LifecycleStatus(StrEnum):
    SETUP_REQUIRED = "SETUP_REQUIRED"
    READY_FOR_LOGIN = "READY_FOR_LOGIN"
    ARCHIVED = "ARCHIVED"


class GroupCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    capacity: int = Field(default=300, ge=1, le=5000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    capacity: int | None = Field(default=None, ge=1, le=5000)
    status: Literal["ACTIVE", "PAUSED"] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None


EMPLOYEE_ROLES = ("ADMINISTRATOR", "ACCOUNT_OPERATOR", "AUDITOR")
EMPLOYEE_STATUSES = ("ACTIVE", "INVITED", "SUSPENDED", "DEACTIVATED")


def _clean_employee_email(value: str) -> str:
    normalized = value.strip().lower()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", normalized):
        raise ValueError("Enter a valid employee email address")
    return normalized


class EmployeeCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    role: Literal["ADMINISTRATOR", "ACCOUNT_OPERATOR", "AUDITOR"] = "ACCOUNT_OPERATOR"
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _clean_employee_email(value)


class EmployeeLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _clean_employee_email(value)


class EmployeePasswordSet(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class EmployeeIdentityResponse(BaseModel):
    id: str
    full_name: str
    email: str
    role: str


class EmployeePermissionsResponse(BaseModel):
    is_admin: bool
    group_ids: list[str]
    can_manage_workspace: bool
    can_operate_accounts: bool


class EmployeeAuthResponse(BaseModel):
    employee: EmployeeIdentityResponse
    permissions: EmployeePermissionsResponse


class AuthStatusResponse(BaseModel):
    authenticated: bool
    setup_required: bool
    session: EmployeeAuthResponse | None = None


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    role: Literal["ADMINISTRATOR", "ACCOUNT_OPERATOR", "AUDITOR"] | None = None
    status: Literal["ACTIVE", "INVITED", "SUSPENDED", "DEACTIVATED"] | None = None
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return _clean_employee_email(value) if value is not None else None


class EmployeeGroupAccessInput(BaseModel):
    group_id: str = Field(min_length=1, max_length=64)
    access_level: Literal["VIEWER", "OPERATOR", "MANAGER"] = "OPERATOR"


class EmployeeGroupAccessUpdate(BaseModel):
    assignments: list[EmployeeGroupAccessInput] = Field(default_factory=list, max_length=500)


class EmployeeGroupAccessResponse(BaseModel):
    group_id: str
    group_name: str
    access_level: Literal["VIEWER", "OPERATOR", "MANAGER"]


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    full_name: str
    email: str
    role: str
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
    group_access: list["EmployeeGroupAccessResponse"] = Field(default_factory=list)


class NewsletterAgentChainEntry(BaseModel):
    priority: int = Field(ge=1, le=8)
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    provider: Literal["openrouter", "openai"] = "openrouter"
    model: str = Field(default="", max_length=160)
    min_confidence: float = Field(default=0.75, ge=0, le=1)
    max_calls: int = Field(default=1, ge=1, le=8)
    timeout_seconds: int = Field(default=20, ge=2, le=120)
    vision_enabled: bool = False
    health: str = "HEALTHY"
    consecutive_failures: int = Field(default=0, ge=0, le=100)
    last_success_at: str = ""
    last_failure_at: str = ""
    last_latency_ms: int = Field(default=0, ge=0, le=600000)
    last_error: str = Field(default="", max_length=240)
    rate_limited_until: str = ""


class WorkspaceSettingsUpdate(BaseModel):
    default_group_capacity: int | None = Field(default=None, ge=1, le=5000)
    default_login_concurrency: int | None = Field(default=None, ge=1, le=50)
    max_concurrent_operations: int | None = Field(default=None, ge=1, le=50)
    max_active_profiles: int | None = Field(default=None, ge=1, le=1000)
    observer_interval_ms: int | None = Field(default=None, ge=500, le=60000)
    session_idle_timeout_minutes: int | None = Field(default=None, ge=5, le=1440)
    auto_close_completed_sessions: bool | None = None
    retain_audit_days: int | None = Field(default=None, ge=30, le=3650)
    telegram_enabled: bool | None = None
    telegram_bot_token: str | None = Field(default=None, max_length=256)
    telegram_chat_id: str | None = Field(default=None, max_length=160)
    email_enabled: bool | None = None
    email_provider: Literal["NONE", "RESEND", "SMTP"] | None = None
    email_recipient_email: str | None = Field(default=None, max_length=320)
    email_from_address: str | None = Field(default=None, max_length=320)
    resend_api_key: str | None = Field(default=None, max_length=512)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=320)
    smtp_password: str | None = Field(default=None, max_length=512)
    smtp_use_tls: bool | None = None
    notify_employee_logins: bool | None = None
    notify_group_reports: bool | None = None
    notify_account_status: bool | None = None
    notify_operation_failures: bool | None = None
    newsletter_agent_enabled: bool | None = None
    newsletter_agent_provider: Literal["openrouter"] | None = None
    newsletter_agent_model: str | None = Field(default=None, min_length=1, max_length=160)
    newsletter_agent_temperature: float | None = Field(default=None, ge=0, le=1)
    newsletter_agent_max_steps: int | None = Field(default=None, ge=1, le=24)
    newsletter_agent_timeout_seconds: int | None = Field(default=None, ge=2, le=120)
    newsletter_agent_shadow_mode: bool | None = None
    newsletter_agent_live_execution_enabled: bool | None = None
    newsletter_agent_learning_mode: bool | None = None
    newsletter_agent_primary_model: str | None = Field(default=None, min_length=1, max_length=160)
    newsletter_agent_secondary_model: str | None = Field(default=None, max_length=160)
    newsletter_agent_free_router_model: str | None = Field(default=None, min_length=1, max_length=160)
    newsletter_agent_max_ai_calls: int | None = Field(default=None, ge=0, le=4)
    newsletter_agent_min_confidence: float | None = Field(default=None, ge=0, le=1)
    newsletter_agent_vision_enabled: bool | None = None
    newsletter_agent_vision_max_calls: int | None = Field(default=None, ge=0, le=4)
    newsletter_agent_skill_replay_enabled: bool | None = None
    newsletter_agent_deterministic_resolver_enabled: bool | None = None
    newsletter_agent_automatic_relearning_enabled: bool | None = None
    newsletter_agent_chain: list[NewsletterAgentChainEntry] | None = Field(default=None, max_length=8)
    openrouter_api_key: str | None = Field(default=None, max_length=512)
    openai_api_key: str | None = Field(default=None, max_length=512)
    two_captcha_api_key: str | None = Field(default=None, max_length=512)
    two_captcha_enabled: bool | None = None
    ovh_endpoint: Literal["ovh-eu", "ovh-ca", "ovh-us"] | None = None
    ovh_application_key: str | None = Field(default=None, max_length=256)
    ovh_application_secret: str | None = Field(default=None, max_length=256)
    ovh_consumer_key: str | None = Field(default=None, max_length=256)
    ovh_clear_credentials: bool | None = None


class WorkspaceSettingsResponse(BaseModel):
    id: str
    organization_id: str
    organization_name: str
    default_group_capacity: int
    default_login_concurrency: int
    max_concurrent_operations: int
    max_active_profiles: int
    observer_interval_ms: int
    session_idle_timeout_minutes: int
    auto_close_completed_sessions: bool
    retain_audit_days: int
    runtime_environment: str
    runtime_browser_launch_enabled: bool
    runtime_observer_enabled: bool
    runtime_worker_enabled: bool
    runtime_redis_configured: bool
    updated_at: datetime
    telegram_enabled: bool
    telegram_configured: bool
    telegram_chat_id: str | None
    email_enabled: bool
    email_provider: Literal["NONE", "RESEND", "SMTP"]
    email_configured: bool
    email_recipient_email: str | None
    email_from_address: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_use_tls: bool
    notify_employee_logins: bool
    notify_group_reports: bool
    notify_account_status: bool
    notify_operation_failures: bool
    newsletter_agent_enabled: bool
    newsletter_agent_provider: Literal["openrouter"]
    newsletter_agent_model: str
    newsletter_agent_temperature: float
    newsletter_agent_max_steps: int
    newsletter_agent_timeout_seconds: int
    newsletter_agent_shadow_mode: bool
    newsletter_agent_live_execution_enabled: bool
    newsletter_agent_learning_mode: bool
    newsletter_agent_primary_model: str
    newsletter_agent_secondary_model: str
    newsletter_agent_free_router_model: str
    newsletter_agent_max_ai_calls: int
    newsletter_agent_min_confidence: float
    newsletter_agent_vision_enabled: bool
    newsletter_agent_vision_max_calls: int
    newsletter_agent_skill_replay_enabled: bool
    newsletter_agent_deterministic_resolver_enabled: bool
    newsletter_agent_automatic_relearning_enabled: bool
    newsletter_agent_chain: list[NewsletterAgentChainEntry]
    openrouter_api_key_configured: bool
    openai_api_key_configured: bool
    two_captcha_api_key_configured: bool
    two_captcha_enabled: bool
    ovh_endpoint: Literal["ovh-eu", "ovh-ca", "ovh-us"]
    ovh_credentials_configured: bool


class OvhPowerRequest(BaseModel):
    resource_type: Literal["vps", "instance"]
    service_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    instance_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    action: Literal["start", "stop"]


class OvhProxyGroupLinkRequest(BaseModel):
    proxy_group: str = Field(min_length=1, max_length=80)
    resource_type: Literal["vps", "instance"]
    service_name: str = Field(min_length=1, max_length=128)
    instance_id: str | None = Field(default=None, max_length=128)

    @field_validator("proxy_group")
    @classmethod
    def clean_proxy_group(cls, value: str) -> str:
        return " ".join(value.split())


class NotificationSettingsUpdate(BaseModel):
    telegram_enabled: bool | None = None
    telegram_bot_token: str | None = Field(default=None, max_length=256)
    telegram_chat_id: str | None = Field(default=None, max_length=160)
    email_enabled: bool | None = None
    email_provider: Literal["NONE", "RESEND", "SMTP"] | None = None
    email_recipient_email: str | None = Field(default=None, max_length=320)
    email_from_address: str | None = Field(default=None, max_length=320)
    resend_api_key: str | None = Field(default=None, max_length=512)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=320)
    smtp_password: str | None = Field(default=None, max_length=512)
    smtp_use_tls: bool | None = None
    notify_employee_logins: bool | None = None
    notify_group_reports: bool | None = None
    notify_account_status: bool | None = None
    notify_operation_failures: bool | None = None


class NotificationSettingsResponse(BaseModel):
    telegram_enabled: bool
    telegram_configured: bool
    telegram_chat_id: str | None
    email_enabled: bool
    email_provider: Literal["NONE", "RESEND", "SMTP"]
    email_configured: bool
    email_recipient_email: str | None
    email_from_address: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_use_tls: bool
    notify_employee_logins: bool
    notify_group_reports: bool
    notify_account_status: bool
    notify_operation_failures: bool
    updated_at: datetime


class NotificationTestRequest(BaseModel):
    channel: Literal["telegram", "email"]


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    password: str = Field(min_length=12, max_length=256)


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None
    capacity: int
    account_count: int
    ready_count: int
    issue_count: int = 0
    status: str


class AccountStructuredInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=2048)
    recovery: str = Field(default="", max_length=320)
    two_fa: str = Field(default="", max_length=2048, alias="2fa")
    group_id: str
    proxy_label: str | None = Field(default=None, max_length=160)
    proxy_region: str | None = Field(default=None, max_length=80)
    proxy: str | None = Field(default=None, max_length=512)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class BulkPreviewRequest(BaseModel):
    raw_lines: str = Field(min_length=1, max_length=1_000_000)
    group_id: str


class BulkImportRequest(BulkPreviewRequest):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class PreviewRow(BaseModel):
    row_number: int
    email: str | None = None
    recovery_masked: str | None = None
    two_fa_configured: bool = False
    valid: bool
    duplicate: bool = False
    error: str | None = None


class BulkPreviewResponse(BaseModel):
    total_rows: int
    valid_rows: int
    duplicate_rows: int
    invalid_rows: int
    rows: list[PreviewRow]


class ProxyMetadata(BaseModel):
    id: str | None
    label: str | None
    endpoint: str | None = None
    region: str | None
    status: str


class ProfileMetadata(BaseModel):
    id: str | None
    storage_reference: str | None
    status: str


class AccountResponse(BaseModel):
    id: str
    email: str
    group_id: str
    group_name: str
    lifecycle_status: str
    readiness_status: str
    login_status: str
    gmail_status: str
    secret_status: str
    recovery_masked: str | None
    two_fa_configured: bool
    proxy: ProxyMetadata
    tags: list[str] = Field(default_factory=list)
    profile: ProfileMetadata
    created_at: datetime
    updated_at: datetime


class AccountMetadataUpdate(BaseModel):
    proxy_label: str | None = Field(default=None, max_length=160)
    proxy_region: str | None = Field(default=None, max_length=80)
    proxy: str | None = Field(default=None, max_length=512)

    @field_validator("proxy_label", "proxy_region")
    @classmethod
    def clean_metadata(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None and value.strip() else None


class AccountTagsUpdate(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(" ".join(tag.split()) for tag in value if tag.strip()))
        if any(len(tag) > 40 for tag in normalized):
            raise ValueError("Tags must be 40 characters or fewer")
        return normalized


class GoDaddyAccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    environment: Literal["production", "ote"] = "production"
    api_key: str = Field(min_length=1, max_length=512)
    api_secret: str = Field(default="", max_length=512)


class GoDaddyAccountUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    environment: Literal["production", "ote"] = "production"
    api_key: str | None = Field(default=None, max_length=512)
    api_secret: str | None = Field(default=None, max_length=512)


class GoDaddyDnsRecord(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["A", "AAAA", "CAA", "CNAME", "MX", "SRV", "TXT"]
    data: str = Field(min_length=1, max_length=4096)
    ttl: int = Field(default=3600, ge=600, le=86400)
    priority: int | None = Field(default=None, ge=0, le=65535)
    weight: int | None = Field(default=None, ge=0, le=65535)
    port: int | None = Field(default=None, ge=1, le=65535)
    service: str | None = Field(default=None, max_length=64)
    protocol: str | None = Field(default=None, max_length=16)
    flag: int | None = Field(default=None, ge=0, le=255)
    tag: str | None = Field(default=None, max_length=32)


class GoDaddyDnsGroupReplace(BaseModel):
    records: list[GoDaddyDnsRecord] = Field(max_length=100)


class GroupMoveRequest(BaseModel):
    destination_group_id: str = Field(min_length=1, max_length=64)


class AccountDeleteRequest(BaseModel):
    confirmation_email: str = Field(min_length=3, max_length=320)

    @field_validator("confirmation_email")
    @classmethod
    def normalize_confirmation(cls, value: str) -> str:
        return value.strip().lower()


class AccountListResponse(BaseModel):
    items: list[AccountResponse]
    page: int
    page_size: int
    total: int
    status_counts: dict[str, int] = Field(default_factory=dict)


class BrowserProfileResponse(BaseModel):
    id: str
    account_id: str
    account_email: str
    storage_reference: str
    status: str
    lock_state: str
    active_session_id: str | None
    last_opened_at: datetime | None
    last_closed_at: datetime | None
    authenticated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProxyAccountReference(BaseModel):
    id: str
    email: str


class ProxyResponse(BaseModel):
    id: str
    label: str | None
    proxy_group: str | None = None
    region: str | None
    status: str
    account_count: int
    authenticated_count: int
    account_emails: list[str]
    account_refs: list[ProxyAccountReference]
    provider: str | None
    country: str | None
    city: str | None
    public_ip: str | None
    test_status: str
    test_error: str | None
    latency_ms: int | None
    last_tested_at: datetime | None


class ProxyBulkImportRequest(BaseModel):
    """Raw bulk input is accepted once and never returned in API responses."""

    lines: str = Field(min_length=1, max_length=300_000)
    group_name: str | None = Field(default=None, max_length=80)

    @field_validator("group_name")
    @classmethod
    def clean_group_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return " ".join(value.split())


class ProxyBulkImportRow(BaseModel):
    line_number: int
    status: Literal["IMPORTED", "DUPLICATE", "INVALID"]
    message: str
    proxy_id: str | None = None


class ProxyBulkImportResponse(BaseModel):
    imported: int
    duplicates: int
    invalid: int
    rows: list[ProxyBulkImportRow]


class ProxyAssignmentRequest(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=300)
    proxy_id: str | None = Field(default=None, max_length=96)
    proxy: str | None = Field(default=None, max_length=512)
    label: str | None = Field(default=None, max_length=160)
    region: str | None = Field(default=None, max_length=80)


class ProxyGroupDistributionRequest(BaseModel):
    group_id: str = Field(min_length=1, max_length=96)
    proxy_ids: list[str] = Field(min_length=1, max_length=500)
    mode: Literal["equal", "limit"] = "limit"
    accounts_per_proxy: int | None = Field(default=None, ge=1, le=300)


class ProxyGroupUpdateRequest(BaseModel):
    proxy_ids: list[str] = Field(min_length=1, max_length=500)
    group_name: str | None = Field(default=None, max_length=80)

    @field_validator("group_name")
    @classmethod
    def clean_group_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return " ".join(value.split())


class ProxyGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class ProxyUpdate(BaseModel):
    proxy: str | None = Field(default=None, max_length=512)
    label: str | None = Field(default=None, max_length=160)
    region: str | None = Field(default=None, max_length=80)


class ProxyDeleteRequest(BaseModel):
    confirmation: str = Field(min_length=6, max_length=20)


class ProxyTestRequest(BaseModel):
    proxy_ids: list[str] = Field(min_length=1, max_length=300)


class ProxyTestResponse(BaseModel):
    proxy_id: str
    status: str
    public_ip: str | None
    provider: str | None
    country: str | None
    city: str | None
    latency_ms: int | None
    tested_at: datetime
    error: str | None


class ImportSummary(BaseModel):
    imported: int
    duplicates: int
    invalid: int
    account_ids: list[str]


class CredentialUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=2048)
    recovery: str | None = Field(default=None, min_length=1, max_length=320)
    two_fa: str | None = Field(default=None, min_length=1, max_length=2048, alias="2fa")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def require_one_change(self) -> "CredentialUpdate":
        if self.password is None and self.recovery is None and self.two_fa is None:
            raise ValueError("Provide one credential field to update")
        return self


class CredentialRevisionResponse(BaseModel):
    account_id: str
    version: int
    status: str
    secret_status: str
    recovery_masked: str
    two_fa_configured: bool
    created_at: datetime


class CredentialChangePlanCreate(BaseModel):
    password: bool = False
    recovery: bool = False
    two_fa: bool = Field(default=False, alias="2fa")
    recovery_emails: list[str] = Field(default_factory=list, max_length=20)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def require_one_change(self) -> "CredentialChangePlanCreate":
        if not (self.password or self.recovery or self.two_fa):
            raise ValueError("Choose at least one credential change for this account")
        if self.recovery and not self.recovery_emails:
            raise ValueError("Recovery rotation requires at least one real recovery email address")
        return self


class CredentialChangePlanConfirm(BaseModel):
    acknowledged: bool = False
    password: str | None = Field(default=None, min_length=1, max_length=2048)
    two_fa: str | None = Field(default=None, min_length=1, max_length=2048, alias="2fa")
    recovery: str | None = Field(default=None, min_length=1, max_length=320)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def require_acknowledgement(self) -> "CredentialChangePlanConfirm":
        if not self.acknowledged:
            raise ValueError("Confirm that the credential change was completed inside the Google session")
        return self


class CredentialChangePlanResponse(BaseModel):
    id: str
    account_id: str
    account_email: str
    version: int
    status: str
    password_requested: bool
    recovery_requested: bool
    two_fa_requested: bool
    recovery_hint: str | None
    recovery_count: int
    session_id: str | None
    provisioned_two_fa_secret: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    confirmed_at: datetime | None
    completed_at: datetime | None
    failure_reason: str | None


class CredentialRecord(BaseModel):
    id: str
    email: str
    group_id: str
    group_name: str
    secret_status: str
    password_present: bool
    recovery_masked: str | None
    two_fa_configured: bool
    credential_status: str
    active_version: int | None
    latest_version: int
    login_status: str
    profile_status: str
    updated_at: datetime


class CredentialRecordListResponse(BaseModel):
    items: list[CredentialRecord]
    page: int
    page_size: int
    total: int


class CredentialRotateResponse(BaseModel):
    account_id: str
    email: str
    password: str
    recovery_masked: str | None
    two_fa_configured: bool
    version: int


class CredentialPlaintextResponse(BaseModel):
    account_id: str
    email: str
    password: str
    recovery: str
    recovery_masked: str | None
    two_fa_configured: bool
    active_version: int | None
    latest_version: int


OPERATION_TYPES = ("contacts", "security", "labels", "apps", "mail", "addons", "alerts", "preferences")


class OperationDraftCreate(BaseModel):
    operation_type: Literal["contacts", "security", "labels", "apps", "mail", "addons", "alerts", "preferences"]
    configuration: dict[str, str] = Field(default_factory=dict, max_length=20)


class OperationDraftResponse(BaseModel):
    id: str
    account_id: str
    operation_type: str
    version: int
    status: str
    configuration: dict[str, str]
    created_by: str
    created_at: datetime
    updated_at: datetime


class AccountPreferencesUpdate(BaseModel):
    gmail_theme: Literal["default", "dark", "light", "high_contrast"] = "default"
    avatar_filename: str | None = Field(default=None, max_length=255)


class AccountPreferencesApplyRequest(BaseModel):
    change_avatar: bool = True
    change_theme: bool = True
    random_theme: bool = True


class AccountPreferencesResponse(BaseModel):
    account_id: str
    email: str
    gmail_theme: str
    avatar_filename: str | None
    avatar_available: bool
    updated_at: datetime | None


class LoginJobCreate(BaseModel):
    group_id: str | None = None
    account_ids: list[str] = Field(default_factory=list, max_length=300)
    concurrency_limit: int = Field(default=4, ge=1, le=50)


class LoginJobItemResponse(BaseModel):
    id: str
    account_id: str
    account_email: str
    session_id: str | None
    state: str
    attempts: int
    last_error: str | None
    paused_reason: str | None = None
    updated_at: datetime


class LoginJobResponse(BaseModel):
    id: str
    group_id: str | None
    operation_type: str = "login"
    concurrency_limit: int
    status: str
    total_count: int
    queued_count: int
    running_count: int
    succeeded_count: int
    attention_count: int
    failed_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    items: list[LoginJobItemResponse] = []


class LoginJobProgressItem(BaseModel):
    id: str
    account_id: str
    account_email: str
    session_id: str | None
    state: str
    attempts: int
    last_error: str | None
    paused_reason: str | None
    updated_at: datetime
    completed_at: datetime | None
    confirmed_messages: int | None = None
    reviewed_message_count: int | None = None
    messages_needing_review: int | None = None


class LoginJobProgressResponse(BaseModel):
    id: str
    status: str
    concurrency_limit: int
    total_count: int
    queued_count: int
    running_count: int
    succeeded_count: int
    attention_count: int
    failed_count: int
    cancelled_count: int
    progress_percent: int
    states: dict[str, int]
    items: list[LoginJobProgressItem]


class SessionStartRequest(BaseModel):
    purpose: Literal["login", "reuse", "contacts", "labels_markers", "security", "addons"] = "login"


class MailWorkflowRequest(BaseModel):
    subject: str = Field(default="", max_length=200)
    action: Literal["spam_to_inbox", "promotions_to_spam", "reply", "confirmation_review"]
    match_mode: Literal["contains", "exact"] = "contains"
    max_messages: int = Field(default=100, ge=1, le=100)
    review_mode: Literal["handoff", "queue"] = "handoff"
    review_email_state: Literal["unread", "read"] = "unread"
    reply_mode: Literal["specific", "random"] = "specific"
    reply_body: str | None = Field(default=None, max_length=10000)

    @field_validator("subject")
    @classmethod
    def clean_subject(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        return cleaned

    @model_validator(mode="after")
    def validate_reply_body(self) -> "MailWorkflowRequest":
        if not self.subject and not (self.action == "confirmation_review" and self.review_mode == "queue"):
            raise ValueError("Enter a subject line")
        if self.action == "confirmation_review":
            # A handoff leaves exactly one matched message open for an
            # operator. A queue may inspect several matched messages in a
            # bounded pass, but never follows a message link.
            if self.review_mode == "handoff":
                self.max_messages = 1
            self.reply_body = None
            return self
        if self.action == "reply" and self.reply_mode == "specific":
            cleaned = (self.reply_body or "").strip()
            if not cleaned:
                raise ValueError("Enter a reply message")
            self.reply_body = cleaned
        elif self.reply_body is not None:
            self.reply_body = self.reply_body.strip() or None
        return self


class MailWorkflowBulkRequest(MailWorkflowRequest):
    group_id: str | None = None
    account_ids: list[str] = Field(default_factory=list, max_length=300)
    concurrency_limit: int = Field(default=4, ge=1, le=50)


class AlertsOperationRequest(BaseModel):
    topic_count: int = Field(default=3, ge=1, le=10)
    topics: list[str] | None = Field(default=None, min_length=1, max_length=10)


class AlertTopicResponse(BaseModel):
    id: str
    topic: str
    category: str
    active: bool
    created_at: datetime
    updated_at: datetime


class AuditEventResponse(BaseModel):
    id: str
    action: str
    target_type: str
    target_id: str
    result: str
    metadata: dict
    created_at: datetime


class SessionEventCreate(BaseModel):
    event_type: Literal["LOGIN_COMPLETED", "VERIFICATION_REQUIRED", "UNKNOWN_PAGE", "CONTACTS_IMPORTED", "SECURITY_COMPLETED", "HEARTBEAT"]
    detail: str | None = Field(default=None, max_length=300)


class PageObservationCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=5000)


class ContactFileResponse(BaseModel):
    id: str
    original_filename: str
    media_type: str
    size_bytes: int
    contact_count: int
    status: str
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContactsImportRequest(BaseModel):
    file_id: str | None = Field(default=None, min_length=1, max_length=96)
    emails: str | None = Field(default=None, min_length=1, max_length=30000)

    @model_validator(mode="after")
    def require_asset(self):
        if not self.file_id and not self.emails:
            raise ValueError("Choose a contact file")
        return self


class ContactsImportResponse(BaseModel):
    session_id: str
    account_id: str
    contact_file_id: str | None
    email_count: int
    status: str


class BrowserSessionResponse(BaseModel):
    id: str
    account_id: str
    account_email: str
    profile_id: str
    profile_storage_reference: str
    profile_status: str
    profile_saved: bool = False
    profile_authenticated_at: datetime | None = None
    state: str
    login_state: str
    purpose: str = "login"
    execution_mode: Literal["SKILL_REPLAY", "DETERMINISTIC", "AGENT_LEARNING", "LIVE_FORM", "MANUAL"] | None = None
    action: str = "Login"
    operation_status: str = "NOT_FINISHED"
    status_detail: str | None = None
    duration_seconds: int | None = None
    runtime_type: str
    browser_opened: bool
    observer_attached: bool = False
    automation_hint: str | None = None
    session_url: str
    opened_at: datetime
    last_event_at: datetime
    closed_at: datetime | None
    failure_reason: str | None


class PageObservationResponse(BaseModel):
    signal: str
    action: str
    session: BrowserSessionResponse


# ---------------------------------------------------------------------------
# Newsletter automation domain (Phase 1: registry foundation only).
# ---------------------------------------------------------------------------

NEWSLETTER_GROUP_STATUSES = ("ACTIVE", "DISABLED")
NEWSLETTER_STATUSES = ("ACTIVE", "TEST_REQUIRED", "AUTOMATION_BROKEN", "DISABLED")
NEWSLETTER_DISCOVERY_STATUSES = ("NOT_DISCOVERED", "DISCOVERING", "DISCOVERED", "DISCOVERY_BLOCKED", "FAILED")
NEWSLETTER_PROFILE_STATUSES = ("DISCOVERED", "CANDIDATE", "TESTING", "TEST_PASSED", "TEST_FAILED", "ACTIVE", "SUPERSEDED", "REJECTED")
NEWSLETTER_PROFILE_CREATABLE_STATUSES = ("DISCOVERED", "CANDIDATE", "TESTING", "TEST_PASSED", "TEST_FAILED")
NEWSLETTER_SUBSCRIPTION_STATES = ("PENDING", "SUBMITTED", "WAITING_CONFIRMATION", "CONFIRMED", "SKIPPED", "FAILED", "MANUAL_REVIEW", "BLOCKED_ANTIBOT")
NEWSLETTER_JOB_STATUSES = ("DRAFT", "QUEUED", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "FAILED")
NEWSLETTER_TASK_STATES = ("PENDING", "QUEUED", "PROCESSING", "SUBMITTED", "WAITING_CONFIRMATION", "CONFIRMED", "SKIPPED", "RETRY_PENDING", "FAILED", "MANUAL_REVIEW", "CANCELLED")
NEWSLETTER_PROFILE_PROMOTABLE_STATUSES = ("TEST_PASSED", "SUPERSEDED", "ACTIVE")
# Statuses an operator can run a signup against. ``TESTING`` is excluded because
# a session is already using that version; ``DISCOVERED``, ``SUPERSEDED`` and
# ``REJECTED`` are excluded because nobody has adopted that configuration yet.
NEWSLETTER_SIGNABLE_PROFILE_STATUSES = ("CANDIDATE", "TEST_FAILED", "TEST_PASSED", "ACTIVE")
NEWSLETTER_PROFILE_ROLLBACK_STATUSES = ("SUPERSEDED", "TEST_PASSED", "ACTIVE")
NEWSLETTER_PROFILE_REJECTABLE_STATUSES = ("DISCOVERED", "CANDIDATE", "TESTING", "TEST_FAILED")
NEWSLETTER_FORBIDDEN_CONFIG_KEY_PARTS = ("password", "recovery", "2fa", "totp", "otp", "cookie", "token", "secret")

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


def normalize_newsletter_domain(value: str) -> str:
    candidate = (value or "").strip().lower()
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    candidate = candidate.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip(".")
    if not candidate or ".." in candidate or not _DOMAIN_RE.match(candidate) or not any(part for part in candidate.split(".") if part):
        raise ValueError("Enter a valid domain name")
    return candidate


def normalize_newsletter_url(value: str) -> str:
    candidate = (value or "").strip()
    parsed = urlsplit(candidate)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("Enter a valid http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Newsletter URLs cannot contain credentials or fragments")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Newsletter URLs must use a public hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified):
        raise ValueError("Newsletter URLs must use a public hostname")
    return candidate


class NewsletterGroupCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Enter a newsletter group name")
        return cleaned


class NewsletterGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    status: Literal["ACTIVE", "DISABLED"] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Enter a newsletter group name")
        return cleaned


class NewsletterGroupResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    description: str | None
    status: str
    newsletter_count: int
    created_at: datetime
    updated_at: datetime


class NewsletterDirectorySeedResponse(BaseModel):
    requested_count: int = Field(ge=0)
    created_count: int = Field(ge=0)
    existing_count: int = Field(ge=0)
    preserved_aliases: list[dict[str, str]] = Field(default_factory=list, max_length=10)
    groups: list[NewsletterGroupResponse] = Field(default_factory=list, max_length=500)


class NewsletterCatalogImportResponse(BaseModel):
    requested_count: int = Field(ge=0)
    created_count: int = Field(ge=0)
    existing_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    created_groups: int = Field(ge=0)
    groups: list[NewsletterGroupResponse] = Field(default_factory=list, max_length=500)


class NewsletterCreate(BaseModel):
    group_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=2, max_length=160)
    website_url: str | None = Field(default=None, max_length=500)
    signup_url: str | None = Field(default=None, max_length=500)
    confirmation_required: bool = False
    allowed_confirmation_domains: list[str] = Field(default_factory=list, max_length=50)
    sender_patterns: list[str] = Field(default_factory=list, max_length=50)
    subject_patterns: list[str] = Field(default_factory=list, max_length=50)
    discovery_status: Literal["NOT_DISCOVERED", "DISCOVERING", "DISCOVERED", "DISCOVERY_BLOCKED", "FAILED"] = "NOT_DISCOVERED"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("website_url", "signup_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return normalize_newsletter_url(value) if value else None

    @field_validator("allowed_confirmation_domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            if len(item) > 240:
                raise ValueError("Domain is too long")
            normalized = normalize_newsletter_domain(item)
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @field_validator("sender_patterns", "subject_patterns")
    @classmethod
    def bound_patterns(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            if len(item) > 200:
                raise ValueError("Pattern is too long")
            if item.strip():
                result.append(item.strip())
        return result


class NewsletterUpdate(BaseModel):
    group_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=2, max_length=160)
    website_url: str | None = Field(default=None, max_length=500)
    signup_url: str | None = Field(default=None, max_length=500)
    status: Literal["ACTIVE", "TEST_REQUIRED", "AUTOMATION_BROKEN", "DISABLED"] | None = None
    confirmation_required: bool | None = None
    allowed_confirmation_domains: list[str] | None = Field(default=None, max_length=50)
    sender_patterns: list[str] | None = Field(default=None, max_length=50)
    subject_patterns: list[str] | None = Field(default=None, max_length=50)
    discovery_status: Literal["NOT_DISCOVERED", "DISCOVERING", "DISCOVERED", "DISCOVERY_BLOCKED", "FAILED"] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None

    @field_validator("website_url", "signup_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return normalize_newsletter_url(value) if value else None

    @field_validator("allowed_confirmation_domains")
    @classmethod
    def normalize_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            if len(item) > 240:
                raise ValueError("Domain is too long")
            normalized = normalize_newsletter_domain(item)
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @field_validator("sender_patterns", "subject_patterns")
    @classmethod
    def bound_patterns(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [item.strip() for item in value if len(item) <= 200 and item.strip()]


class NewsletterResponse(BaseModel):
    id: str
    organization_id: str
    group_id: str
    group_name: str
    name: str
    website_url: str | None
    signup_url: str | None
    status: str
    registry_status: str
    failure_reason: str | None
    failure_at: datetime | None
    confirmation_required: bool
    allowed_confirmation_domains: list[str]
    sender_patterns: list[str]
    subject_patterns: list[str]
    discovery_status: str
    last_discovered_at: datetime | None
    last_verified_at: datetime | None
    profile_versions: int
    active_profile_version: int | None
    signup_score: int | None = None
    subscribed_count: int = 0
    confirmed_count: int = 0
    review_count: int = 0
    scored_count: int = 0
    created_at: datetime
    updated_at: datetime


class NewsletterFieldResponse(BaseModel):
    field_key: str = Field(min_length=1, max_length=80)
    field_type: str = Field(min_length=1, max_length=40)
    selector: str = Field(max_length=300)
    selector_candidates: list[str] = Field(default_factory=list, max_length=8)
    name: str = Field(default="", max_length=120)
    id: str = Field(default="", max_length=120)
    aria_label: str = Field(default="", max_length=200)
    label: str = Field(default="", max_length=200)
    placeholder: str = Field(default="", max_length=200)
    autocomplete: str = Field(default="", max_length=80)
    required: bool | None = None
    visible: bool = True
    enabled: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = Field(default="scrapling", max_length=40)
    options: list[str] = Field(default_factory=list, max_length=20)
    option_values: list[str] = Field(default_factory=list, max_length=20)


class NewsletterDiscoveryCandidateResponse(BaseModel):
    url: str
    source: str
    confidence: float = Field(ge=0.0, le=1.0)
    page_reachable: bool
    http_status: int | None = Field(default=None, ge=100, le=599)
    # "normal" for the plain HTTP fetcher, "stealth" when a challenge forced a
    # browser-backed re-read of the same public page.
    fetch_mode: str = Field(default="normal", max_length=24)
    form_action: str = ""
    form_method: str = ""
    child_frame_present: bool = False
    challenge_indicators: list[str] = Field(default_factory=list, max_length=5)
    email_selector: str | None = Field(default=None, max_length=240)
    submit_selector: str | None = Field(default=None, max_length=240)
    submit_controls: list[dict] = Field(default_factory=list, max_length=20)
    consent_selector: str | None = Field(default=None, max_length=240)
    consent_controls: list[dict] = Field(default_factory=list, max_length=20)
    success_signals: list[str] = Field(default_factory=list, max_length=5)
    failure_signals: list[str] = Field(default_factory=list, max_length=5)
    confirmation_domain_hints: list[str] = Field(default_factory=list, max_length=5)
    evidence: list[str] = Field(default_factory=list, max_length=6)
    fields: list[NewsletterFieldResponse] = Field(default_factory=list, max_length=40)
    configuration: dict[str, object] = Field(default_factory=dict, max_length=40)


class NewsletterDiscoveryResponse(BaseModel):
    newsletter_id: str
    started_at: datetime
    completed_at: datetime
    website_reachable: bool
    discovery_blocked: bool = False
    blocked_reasons: list[str] = Field(default_factory=list, max_length=3)
    inspected_url_count: int = Field(ge=0, le=8)
    stealth_fetch_count: int = Field(default=0, ge=0, le=8)
    recommended_candidate_url: str | None = None
    candidates: list[NewsletterDiscoveryCandidateResponse] = Field(default_factory=list, max_length=6)
    warnings: list[str] = Field(default_factory=list, max_length=6)


class NewsletterBulkDiscoveryItemResponse(BaseModel):
    newsletter_id: str
    name: str
    website_url: str | None
    discovery_status: str
    registry_status: str
    failure_reason: str | None
    inspected_url_count: int = Field(ge=0, le=8)
    candidate_count: int = Field(ge=0, le=6)


class NewsletterBulkDiscoveryResponse(BaseModel):
    started_at: datetime
    completed_at: datetime
    requested_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    discovered_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    items: list[NewsletterBulkDiscoveryItemResponse] = Field(default_factory=list)


class NewsletterHealthResponse(BaseModel):
    newsletter_id: str
    status: str
    newsletter_status: str
    active_profile_version: int | None
    window_size: int = Field(ge=0, le=10)
    success_count: int = Field(ge=0, le=10)
    failure_count: int = Field(ge=0, le=10)
    manual_review_count: int = Field(ge=0, le=10)
    selector_failure_count: int = Field(ge=0, le=10)
    test_required_threshold: int = Field(ge=1, le=10)
    automation_broken_threshold: int = Field(ge=1, le=10)
    bulk_blocked: bool
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_manual_review_at: datetime | None
    last_detail: str | None


class NewsletterRediscoveryResponse(BaseModel):
    newsletter_id: str
    discovery: NewsletterDiscoveryResponse
    health: NewsletterHealthResponse
    candidate_profile: dict | None = None


def bound_newsletter_configuration(value: dict[str, object]) -> dict[str, object]:
    """Bound and sanitize an automation profile's configuration object.

    Shared by profile creation and profile update so the credential boundary
    and the size limits cannot drift apart between the two write paths.
    """
    def validate_value(item: object, depth: int = 0) -> object:
        if depth > 3:
            raise ValueError("Configuration nesting is too deep")
        if isinstance(item, str):
            if len(item) > 500:
                raise ValueError("Configuration value is too long")
            return item
        if isinstance(item, (bool, int, float)) or item is None:
            return item
        if isinstance(item, list):
            if len(item) > 40:
                raise ValueError("Configuration list is too long")
            return [validate_value(child, depth + 1) for child in item]
        if isinstance(item, dict):
            if len(item) > 40:
                raise ValueError("Configuration object has too many keys")
            return {str(key): validate_value(child, depth + 1) for key, child in item.items()}
        raise ValueError("Configuration values must be JSON-compatible")

    bounded: dict[str, object] = {}
    for key, item in value.items():
        clean_key = " ".join(key.strip().split())
        lower = clean_key.lower()
        if not clean_key:
            raise ValueError("Configuration keys cannot be empty")
        if len(clean_key) > 64:
            raise ValueError("Configuration key is too long")
        if any(part in lower for part in NEWSLETTER_FORBIDDEN_CONFIG_KEY_PARTS):
            raise ValueError(f"Configuration key {clean_key!r} is not allowed")
        bounded[clean_key] = validate_value(item)
    return bounded


class NewsletterProfileCreate(BaseModel):
    newsletter_id: str = Field(min_length=1, max_length=64)
    status: Literal["DISCOVERED", "CANDIDATE", "TESTING", "TEST_PASSED", "TEST_FAILED"] = "DISCOVERED"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    configuration: dict[str, object] = Field(default_factory=dict, max_length=40)

    @field_validator("configuration")
    @classmethod
    def bound_configuration(cls, value: dict[str, object]) -> dict[str, object]:
        return bound_newsletter_configuration(value)


class NewsletterProfileUpdate(BaseModel):
    """An operator's correction to a reviewed profile.

    Saved as a new candidate version rather than editing the reviewed record.
    """

    configuration: dict[str, object] = Field(min_length=1, max_length=40)

    @field_validator("configuration")
    @classmethod
    def bound_configuration(cls, value: dict[str, object]) -> dict[str, object]:
        return bound_newsletter_configuration(value)


class NewsletterProfileResponse(BaseModel):
    id: str
    organization_id: str
    newsletter_id: str
    version: int
    status: str
    confidence: float
    configuration_keys: list[str]
    discovered_at: datetime | None
    tested_at: datetime | None
    promoted_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NewsletterProfileDetailResponse(NewsletterProfileResponse):
    configuration: dict[str, object]


class NewsletterSkillResponse(BaseModel):
    newsletter_id: str
    status: str
    version: int | None = None
    confidence: float | None = None
    website_host: str | None = None
    action_count: int = 0
    use_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    validation_confidence: float = 0.0
    last_used_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_validated_at: datetime | None = None


class NewsletterSubscriptionResponse(BaseModel):
    id: str
    account_id: str
    account_email: str
    account_group_id: str
    account_group_name: str
    newsletter_id: str
    newsletter_name: str
    newsletter_group_id: str
    newsletter_group_name: str
    state: str
    submitted_at: datetime | None
    confirmed_at: datetime | None
    last_checked_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class NewsletterSubscriptionListResponse(BaseModel):
    items: list[NewsletterSubscriptionResponse]
    total: int


class NewsletterSignupTestRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=96)
    profile_id: str | None = Field(default=None, max_length=64)
    auto_confirm: bool = True
    # Approved, non-secret values for additional public signup fields. These
    # are used only by the live session and are never written to the profile.
    test_identity: dict[str, str] = Field(default_factory=dict, max_length=40)
    # Write-only publisher credential for this account/newsletter pair. It is
    # never part of identity values, session metadata, audit, or responses.
    signup_password: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("test_identity")
    @classmethod
    def clean_test_identity(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_identity_values(value)


class NewsletterSignupTestResponse(BaseModel):
    session: BrowserSessionResponse | None
    newsletter_id: str
    profile_id: str
    subscription_id: str
    result: Literal["IN_PROGRESS", "SUBMITTED", "WAITING_CONFIRMATION", "CONFIRMED", "FAILED", "MANUAL_REVIEW", "SKIPPED", "SKIPPED_ALREADY_SUBSCRIBED", "SKIPPED_ALREADY_IN_PROGRESS"]
    signup_mode: Literal["PROFILE", "ADAPTIVE_LEARNING", "LIVE_FORM"] = "PROFILE"
    execution_mode: Literal["SKILL_REPLAY", "DETERMINISTIC", "AGENT_LEARNING", "LIVE_FORM", "MANUAL"] = "DETERMINISTIC"
    agent_status: Literal["UNKNOWN", "NOT_USED", "READY", "INVOKED", "SKILL_REPLAYED", "NO_DECISION"] = "UNKNOWN"
    agent_detail: str | None = None
    confirmation_required: bool = False
    auto_confirm: bool = False
    confirmation_session_id: str | None = None


class NewsletterAccountIdentityUpdate(BaseModel):
    values: dict[str, str] = Field(default_factory=dict, max_length=40)

    @field_validator("values")
    @classmethod
    def clean_values(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_identity_values(value, message="Only non-secret signup fields may be saved")


class NewsletterAccountIdentityResponse(BaseModel):
    account_id: str
    values: dict[str, str]
    updated_at: datetime | None


class NewsletterIdentityGenerateRequest(BaseModel):
    """Preview or commit the generated placeholder profile for one account."""

    # ``True`` writes the merged values into the account's saved identity.
    # ``False`` (the default) is a preview the operator can review or edit.
    commit: bool = False
    # Explicit values that win over both the generated and the saved layer.
    overrides: dict[str, str] = Field(default_factory=dict, max_length=40)

    @field_validator("overrides")
    @classmethod
    def clean_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_identity_values(value)


class NewsletterIdentityGenerateResponse(BaseModel):
    account_id: str
    values: dict[str, str]
    # Which keys were filled by the generator rather than by a saved or
    # supplied value. Lets the UI label exactly what is a placeholder.
    generated_keys: list[str] = Field(default_factory=list, max_length=40)
    committed: bool = False
    updated_at: datetime | None = None


class NewsletterConfirmationTestRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=96)
    profile_id: str = Field(min_length=1, max_length=64)
    subscription_id: str = Field(min_length=1, max_length=96)


class NewsletterConfirmationTestResponse(BaseModel):
    session: BrowserSessionResponse
    newsletter_id: str
    profile_id: str
    subscription_id: str
    result: Literal["IN_PROGRESS", "WAITING_CONFIRMATION", "CONFIRMED", "FAILED", "MANUAL_REVIEW", "SKIPPED"]


class NewsletterJobCreate(BaseModel):
    newsletter_id: str | None = Field(default=None, min_length=1, max_length=64)
    newsletter_group_id: str | None = Field(default=None, min_length=1, max_length=64)
    account_group_id: str = Field(min_length=1, max_length=64)
    concurrency_limit: int = Field(default=4, ge=1, le=10)
    retry_policy_json: dict[str, int | bool | str | list[str]] = Field(default_factory=dict, max_length=12)
    auto_confirm: bool = True

    @model_validator(mode="after")
    def require_meaningful_scope(self) -> "NewsletterJobCreate":
        if not self.newsletter_id and not self.newsletter_group_id:
            raise ValueError("Choose a newsletter or a newsletter group to target")
        return self

    @field_validator("retry_policy_json")
    @classmethod
    def bound_retry_policy(cls, value: dict[str, int | bool | str | list[str]]) -> dict[str, int | bool | str | list[str]]:
        for key in value:
            if len(key) > 40:
                raise ValueError("Retry policy key is too long")
        if value.get("recipient_mode") == "EXTERNAL":
            if value.get("consent_confirmed") is not True:
                raise ValueError("Confirm that you own or have permission to subscribe these external addresses")
            if "headless_browser" in value and not isinstance(value["headless_browser"], bool):
                raise ValueError("headless_browser must be true or false")
            recipients = value.get("external_recipients")
            if not isinstance(recipients, list) or not recipients or len(recipients) > 300:
                raise ValueError("Enter between 1 and 300 external email addresses")
            normalized: list[str] = []
            for recipient in recipients:
                if not isinstance(recipient, str):
                    raise ValueError("External recipients must be email addresses")
                email = recipient.strip().lower()
                if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
                    raise ValueError("Every external recipient must be a valid email address")
                if email not in normalized:
                    normalized.append(email)
            value["external_recipients"] = normalized
            value["headless_browser"] = value.get("headless_browser", True)
        elif "external_recipients" in value or "consent_confirmed" in value:
            raise ValueError("External recipient settings require recipient_mode=EXTERNAL")
        elif "headless_browser" in value:
            raise ValueError("headless_browser is only available for external recipients")
        return value


class NewsletterAccountTargetsRequest(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=5000)

    @field_validator("account_ids")
    @classmethod
    def unique_account_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if len(normalized) != len(value) or len(set(normalized)) != len(normalized):
            raise ValueError("Account IDs must be non-empty and unique")
        return normalized


class NewsletterJobTaskSelection(BaseModel):
    task_ids: list[str] = Field(default_factory=list, max_length=500)


class NewsletterJobBulkDelete(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("job_ids")
    @classmethod
    def unique_job_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if len(normalized) != len(value) or len(set(normalized)) != len(normalized):
            raise ValueError("Job IDs must be non-empty and unique")
        return normalized


class NewsletterTaskResponse(BaseModel):
    id: str
    account_id: str
    account_email: str
    newsletter_id: str
    newsletter_name: str
    state: str
    attempts: int
    profile_version: int | None
    worker_id: str | None
    browser_session_id: str | None
    heartbeat_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class NewsletterJobResponse(BaseModel):
    id: str
    organization_id: str
    newsletter_id: str | None
    newsletter_name: str | None
    newsletter_group_id: str | None
    newsletter_group_name: str | None
    account_group_id: str
    account_group_name: str
    status: str
    concurrency_limit: int
    retry_policy_json: dict
    randomize_newsletters: bool
    random_newsletter_count: int | None
    auto_confirm: bool
    recipient_mode: Literal["SAVED_ACCOUNTS", "EXTERNAL"] = "SAVED_ACCOUNTS"
    external_recipient_count: int = 0
    headless_browser: bool = False
    created_by: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    total_tasks: int
    task_counts: dict
    tasks: list[NewsletterTaskResponse]


class NewsletterJobListResponse(BaseModel):
    items: list[NewsletterJobResponse]
    total: int
