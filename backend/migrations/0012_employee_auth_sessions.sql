-- Local workspace identity. This never stores Gmail account secrets.
CREATE TABLE IF NOT EXISTS employee_auth (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    employee_id VARCHAR(96) NOT NULL UNIQUE REFERENCES employees(id) ON DELETE CASCADE,
    password_hash VARCHAR(256) NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMP,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_employee_auth_organization_id ON employee_auth (organization_id);
CREATE INDEX IF NOT EXISTS ix_employee_auth_employee_id ON employee_auth (employee_id);

CREATE TABLE IF NOT EXISTS employee_sessions (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    employee_id VARCHAR(96) NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_employee_sessions_organization_id ON employee_sessions (organization_id);
CREATE INDEX IF NOT EXISTS ix_employee_sessions_employee_id ON employee_sessions (employee_id);
CREATE INDEX IF NOT EXISTS ix_employee_sessions_token_hash ON employee_sessions (token_hash);
CREATE INDEX IF NOT EXISTS ix_employee_sessions_expires_at ON employee_sessions (expires_at);
