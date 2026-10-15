-- Learned per-website action skills and first-run learning-mode setting.
-- Existing SQLite databases are upgraded by app.db compatibility code; this
-- migration is for deploy pipelines that apply PostgreSQL schema changes.
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS newsletter_agent_learning_mode BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS newsletter_automation_skills (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(96) NOT NULL REFERENCES organizations(id),
    newsletter_id VARCHAR(96) NOT NULL REFERENCES newsletters(id),
    profile_id VARCHAR(64) REFERENCES newsletter_automation_profiles(id),
    version INTEGER NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE',
    skill_json JSON NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    use_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMP,
    last_success_at TIMESTAMP,
    last_failure_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_newsletter_skill_newsletter_version UNIQUE (newsletter_id, version)
);

CREATE INDEX IF NOT EXISTS ix_newsletter_skills_org_newsletter_status
    ON newsletter_automation_skills (organization_id, newsletter_id, status);
