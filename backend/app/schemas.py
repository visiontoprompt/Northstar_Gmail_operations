from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    recovery: str = Field(min_length=1, max_length=320)
    two_fa: str = Field(min_length=1, max_length=2048, alias="2fa")
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
    recovery_masked: str
    two_fa_configured: bool
    proxy: ProxyMetadata
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
    region: str | None
    status: str
    account_count: int
    authenticated_count: int
    account_emails: list[str]
    account_refs: list[ProxyAccountReference]


class ImportSummary(BaseModel):
    imported: int
    duplicates: int
    invalid: int
    account_ids: list[str]


class CredentialUpdate(BaseModel):
    password: str = Field(min_length=1, max_length=2048)
    recovery: str = Field(min_length=1, max_length=320)
    two_fa: str = Field(min_length=1, max_length=2048, alias="2fa")

    model_config = ConfigDict(populate_by_name=True)


class CredentialRevisionResponse(BaseModel):
    account_id: str
    version: int
    status: str
    secret_status: str
    recovery_masked: str
    two_fa_configured: bool
    created_at: datetime


OPERATION_TYPES = ("contacts", "security", "labels", "apps", "mail", "addons", "alerts")


class OperationDraftCreate(BaseModel):
    operation_type: Literal["contacts", "security", "labels", "apps", "mail", "addons", "alerts"]
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
    updated_at: datetime


class LoginJobResponse(BaseModel):
    id: str
    group_id: str | None
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
    purpose: Literal["login", "reuse", "contacts", "labels_markers"] = "login"


class AuditEventResponse(BaseModel):
    id: str
    action: str
    target_type: str
    target_id: str
    result: str
    metadata: dict
    created_at: datetime


class SessionEventCreate(BaseModel):
    event_type: Literal["LOGIN_COMPLETED", "VERIFICATION_REQUIRED", "UNKNOWN_PAGE", "CONTACTS_IMPORTED", "HEARTBEAT"]
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
