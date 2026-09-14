-- Account administration and typed, non-secret operation plans.
-- Account deletion is represented by lifecycle_status=ARCHIVED so retention
-- policy can govern profile and secret cleanup separately.

CREATE TABLE IF NOT EXISTS operation_drafts (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    operation_type VARCHAR(40) NOT NULL,
    version INTEGER NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'DRAFT',
    configuration_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by VARCHAR(96) NOT NULL DEFAULT 'admin_demo',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_operation_draft_account_type_version UNIQUE (account_id, operation_type, version)
);

CREATE INDEX IF NOT EXISTS ix_operation_drafts_org_account ON operation_drafts(organization_id, account_id, created_at DESC);
