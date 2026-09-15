-- Reusable organization-scoped contact import assets.
-- Runtime startup also creates this table from SQLAlchemy metadata.

CREATE TABLE IF NOT EXISTS contact_files (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    original_filename VARCHAR(255) NOT NULL,
    storage_reference VARCHAR(500) NOT NULL,
    media_type VARCHAR(120) NOT NULL DEFAULT 'text/csv',
    size_bytes INTEGER NOT NULL,
    contact_count INTEGER NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'READY',
    last_used_at TIMESTAMPTZ,
    created_by VARCHAR(96) NOT NULL DEFAULT 'admin_demo',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_contact_file_org_checksum UNIQUE (organization_id, checksum)
);

CREATE INDEX IF NOT EXISTS ix_contact_files_org_status ON contact_files(organization_id, status, created_at DESC);
