-- Mail Workflow bulk jobs reuse the audited profile queue while retaining
-- operation-specific configuration for each queued account.
ALTER TABLE login_jobs ADD COLUMN IF NOT EXISTS operation_type VARCHAR(32) NOT NULL DEFAULT 'login';
ALTER TABLE login_jobs ADD COLUMN IF NOT EXISTS operation_config JSON NOT NULL DEFAULT '{}';
