-- Server-backed credential change plans: an audit-safe lifecycle for rotating
-- account credentials without ever automating Google security changes.
--
-- Lifecycle: DRAFT -> READY_FOR_OPERATOR -> GOOGLE_CHANGE_CONFIRMED ->
-- STORED_ENCRYPTED, with FAILED / CANCELLED as terminal outcomes. Only the
-- recovery rotation pool is stored here, encrypted at rest; the actual new
-- password / recovery / 2FA values are encrypted into a new
-- CredentialRevision only when the operator confirms the Google-side change.
--
-- Runtime startup uses SQLAlchemy metadata; this file documents the
-- deployable migration.

CREATE TABLE IF NOT EXISTS credential_change_plans (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    password_requested BOOLEAN NOT NULL DEFAULT 0,
    recovery_requested BOOLEAN NOT NULL DEFAULT 0,
    two_fa_requested BOOLEAN NOT NULL DEFAULT 0,
    recovery_pool_encrypted TEXT,
    recovery_hint VARCHAR(320),
    recovery_count INTEGER NOT NULL DEFAULT 0,
    session_id VARCHAR(96),
    configuration_json JSON NOT NULL,
    failure_reason VARCHAR(500),
    created_by VARCHAR(96) NOT NULL DEFAULT 'admin_demo',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    started_at DATETIME,
    confirmed_at DATETIME,
    completed_at DATETIME,
    CONSTRAINT uq_credential_plan_account_version UNIQUE (account_id, version)
);

CREATE INDEX IF NOT EXISTS ix_credential_plans_org_status ON credential_change_plans (organization_id, status);
CREATE INDEX IF NOT EXISTS ix_credential_plans_account ON credential_change_plans (account_id);