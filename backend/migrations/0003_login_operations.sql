-- Phase 2 preparation: encrypted credential revisions and bulk login control.
-- Runtime startup uses SQLAlchemy metadata; this file documents the deployable migration.

CREATE TABLE IF NOT EXISTS credential_revisions (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    secret_ref VARCHAR(180) NOT NULL UNIQUE,
    encrypted_payload TEXT NOT NULL,
    recovery_hint VARCHAR(320) NOT NULL,
    two_fa_configured BOOLEAN NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE',
    created_by VARCHAR(96) NOT NULL DEFAULT 'admin_demo',
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_credential_revision_account_version UNIQUE (account_id, version)
);

CREATE TABLE IF NOT EXISTS login_jobs (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    group_id VARCHAR(64) REFERENCES account_groups(id),
    concurrency_limit INTEGER NOT NULL DEFAULT 4,
    status VARCHAR(24) NOT NULL DEFAULT 'QUEUED',
    total_count INTEGER NOT NULL DEFAULT 0,
    queued_count INTEGER NOT NULL DEFAULT 0,
    running_count INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    attention_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_by VARCHAR(96) NOT NULL DEFAULT 'admin_demo',
    created_at DATETIME NOT NULL,
    started_at DATETIME,
    completed_at DATETIME,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS login_job_items (
    id VARCHAR(96) PRIMARY KEY,
    job_id VARCHAR(96) NOT NULL REFERENCES login_jobs(id) ON DELETE CASCADE,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    session_id VARCHAR(96) REFERENCES browser_sessions(id),
    state VARCHAR(24) NOT NULL DEFAULT 'QUEUED',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error VARCHAR(500),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_login_job_account UNIQUE (job_id, account_id)
);

CREATE INDEX IF NOT EXISTS ix_login_jobs_organization_status ON login_jobs (organization_id, status);
CREATE INDEX IF NOT EXISTS ix_login_job_items_job_state ON login_job_items (job_id, state);
