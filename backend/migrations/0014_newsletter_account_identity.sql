-- Operator-approved non-secret signup details, encrypted at rest.
CREATE TABLE IF NOT EXISTS newsletter_account_identities (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    encrypted_payload TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_newsletter_identity_org_account UNIQUE (organization_id, account_id)
);
CREATE INDEX IF NOT EXISTS ix_newsletter_account_identities_organization_id ON newsletter_account_identities (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_account_identities_account_id ON newsletter_account_identities (account_id);
