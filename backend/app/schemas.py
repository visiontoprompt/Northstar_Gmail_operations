from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class AccountListResponse(BaseModel):
    items: list[AccountResponse]
    page: int
    page_size: int
    total: int


class ImportSummary(BaseModel):
    imported: int
    duplicates: int
    invalid: int
    account_ids: list[str]


class AuditEventResponse(BaseModel):
    id: str
    action: str
    target_type: str
    target_id: str
    result: str
    metadata: dict
    created_at: datetime


class SessionEventCreate(BaseModel):
    event_type: Literal["LOGIN_COMPLETED", "VERIFICATION_REQUIRED", "HEARTBEAT"]
    detail: str | None = Field(default=None, max_length=300)


class BrowserSessionResponse(BaseModel):
    id: str
    account_id: str
    account_email: str
    profile_id: str
    profile_storage_reference: str
    profile_status: str
    state: str
    login_state: str
    runtime_type: str
    session_url: str
    opened_at: datetime
    last_event_at: datetime
    closed_at: datetime | None
    failure_reason: str | None
