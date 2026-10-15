-- Employee-to-group access assignments. Access levels are a local, audited
-- authorization map; production IdP/RBAC enforcement remains a later boundary.

CREATE TABLE IF NOT EXISTS employee_group_access (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    employee_id VARCHAR(96) NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    group_id VARCHAR(64) NOT NULL REFERENCES account_groups(id) ON DELETE CASCADE,
    access_level VARCHAR(24) NOT NULL DEFAULT 'OPERATOR',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_employee_group_access UNIQUE (employee_id, group_id)
);

CREATE INDEX IF NOT EXISTS ix_employee_group_access_organization_id ON employee_group_access (organization_id);
CREATE INDEX IF NOT EXISTS ix_employee_group_access_employee_id ON employee_group_access (employee_id);
CREATE INDEX IF NOT EXISTS ix_employee_group_access_group_id ON employee_group_access (group_id);
