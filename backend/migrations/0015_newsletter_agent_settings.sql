-- Organization-scoped Newsletter Agent controls. The OpenRouter key is
-- encrypted by the application before it is written to this column.
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_provider VARCHAR(32) NOT NULL DEFAULT 'openrouter';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_model VARCHAR(160) NOT NULL DEFAULT 'openrouter/free';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_temperature REAL NOT NULL DEFAULT 0;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_max_steps INTEGER NOT NULL DEFAULT 12;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_timeout_seconds INTEGER NOT NULL DEFAULT 30;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_shadow_mode BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_live_execution_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS openrouter_api_key_encrypted TEXT;
