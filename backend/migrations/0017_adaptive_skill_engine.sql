-- Newsletter adaptive skill engine settings and lifecycle fields
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_primary_model VARCHAR(160) NOT NULL DEFAULT 'inclusionai/ling-3.0-flash-vl:free';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_secondary_model VARCHAR(160) NOT NULL DEFAULT 'nex-agi/Nex-N2.5-Mini:free';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_free_router_model VARCHAR(160) NOT NULL DEFAULT 'openrouter/free';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_max_ai_calls INTEGER NOT NULL DEFAULT 4;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_min_confidence FLOAT NOT NULL DEFAULT 0.70;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_vision_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_vision_max_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_skill_replay_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_deterministic_resolver_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_automatic_relearning_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_chain JSON;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS openai_api_key_encrypted TEXT;

ALTER TABLE newsletter_automation_skills ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE newsletter_automation_skills ADD COLUMN IF NOT EXISTS validation_confidence FLOAT NOT NULL DEFAULT 0.0;
ALTER TABLE newsletter_automation_skills ADD COLUMN IF NOT EXISTS last_validated_at TIMESTAMP;
