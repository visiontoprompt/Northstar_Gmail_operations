ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS ovh_endpoint VARCHAR(16) NOT NULL DEFAULT 'ovh-eu';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS ovh_credentials_encrypted TEXT;
