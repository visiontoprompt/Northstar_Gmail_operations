CREATE TABLE godaddy_accounts (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    label VARCHAR(120) NOT NULL,
    environment VARCHAR(16) NOT NULL DEFAULT 'production',
    encrypted_credentials TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_godaddy_account_label UNIQUE (organization_id, label)
);
CREATE INDEX ix_godaddy_accounts_organization_id ON godaddy_accounts (organization_id);
