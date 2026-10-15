-- Workspace structure controls: employee directory and organization settings.
-- The development bootstrap uses SQLAlchemy metadata.create_all; production
-- deployments should apply this migration through the normal migration runner.

CREATE TABLE IF NOT EXISTS employees (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    full_name VARCHAR(160) NOT NULL,
    email VARCHAR(320) NOT NULL,
    role VARCHAR(48) NOT NULL DEFAULT 'ACCOUNT_OPERATOR',
    status VARCHAR(24) NOT NULL DEFAULT 'INVITED',
    notes VARCHAR(500),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_employee_org_email UNIQUE (organization_id, email)
);

CREATE INDEX IF NOT EXISTS ix_employees_organization_id ON employees (organization_id);
CREATE INDEX IF NOT EXISTS ix_employees_status ON employees (status);

CREATE TABLE IF NOT EXISTS workspace_settings (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    default_group_capacity INTEGER NOT NULL DEFAULT 300,
    default_login_concurrency INTEGER NOT NULL DEFAULT 4,
    max_concurrent_operations INTEGER NOT NULL DEFAULT 4,
    max_active_profiles INTEGER NOT NULL DEFAULT 50,
    observer_interval_ms INTEGER NOT NULL DEFAULT 1000,
    session_idle_timeout_minutes INTEGER NOT NULL DEFAULT 30,
    auto_close_completed_sessions BOOLEAN NOT NULL DEFAULT TRUE,
    retain_audit_days INTEGER NOT NULL DEFAULT 365,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_workspace_settings_org UNIQUE (organization_id)
);

CREATE INDEX IF NOT EXISTS ix_workspace_settings_organization_id ON workspace_settings (organization_id);
