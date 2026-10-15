-- Newsletter registry and automation foundation (Phase 1: persistent domain
-- model only). Runtime startup uses SQLAlchemy metadata; this file documents
-- the deployable migration for production PostgreSQL deployments.
--
-- No execution behavior (discovery, signup, confirmation, dispatch) is
-- introduced by this migration. Only the durable registry state machine.

CREATE TABLE IF NOT EXISTS newsletter_groups (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    name VARCHAR(120) NOT NULL,
    description VARCHAR(500),
    status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_newsletter_group_org_name UNIQUE (organization_id, name)
);

CREATE INDEX IF NOT EXISTS ix_newsletter_groups_organization_id ON newsletter_groups (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_groups_status ON newsletter_groups (status);

CREATE TABLE IF NOT EXISTS newsletters (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    group_id VARCHAR(64) NOT NULL REFERENCES newsletter_groups(id),
    name VARCHAR(160) NOT NULL,
    website_url VARCHAR(500),
    signup_url VARCHAR(500),
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    confirmation_required BOOLEAN NOT NULL DEFAULT FALSE,
    allowed_confirmation_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
    sender_patterns JSONB NOT NULL DEFAULT '[]'::jsonb,
    subject_patterns JSONB NOT NULL DEFAULT '[]'::jsonb,
    discovery_status VARCHAR(32) NOT NULL DEFAULT 'NOT_DISCOVERED',
    last_discovered_at TIMESTAMPTZ,
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_newsletters_organization_id ON newsletters (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletters_group_id ON newsletters (group_id);
CREATE INDEX IF NOT EXISTS ix_newsletters_status ON newsletters (status);
CREATE INDEX IF NOT EXISTS ix_newsletters_discovery_status ON newsletters (discovery_status);

CREATE TABLE IF NOT EXISTS newsletter_automation_profiles (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    newsletter_id VARCHAR(64) NOT NULL REFERENCES newsletters(id),
    version INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DISCOVERED',
    configuration_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence NUMERIC(6, 5) NOT NULL DEFAULT 0,
    discovered_at TIMESTAMPTZ,
    tested_at TIMESTAMPTZ,
    promoted_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_newsletter_profile_newsletter_version UNIQUE (newsletter_id, version)
);

CREATE INDEX IF NOT EXISTS ix_newsletter_automation_profiles_organization_id ON newsletter_automation_profiles (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_automation_profiles_newsletter_id ON newsletter_automation_profiles (newsletter_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_automation_profiles_status ON newsletter_automation_profiles (status);

CREATE TABLE IF NOT EXISTS newsletter_subscriptions (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id),
    newsletter_id VARCHAR(64) NOT NULL REFERENCES newsletters(id),
    state VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    submitted_at TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    last_error VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_newsletter_subscription_org_account_newsletter UNIQUE (organization_id, account_id, newsletter_id)
);

CREATE INDEX IF NOT EXISTS ix_newsletter_subscriptions_organization_id ON newsletter_subscriptions (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_subscriptions_account_id ON newsletter_subscriptions (account_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_subscriptions_newsletter_id ON newsletter_subscriptions (newsletter_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_subscriptions_state ON newsletter_subscriptions (state);

CREATE TABLE IF NOT EXISTS newsletter_jobs (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    newsletter_id VARCHAR(64) REFERENCES newsletters(id),
    newsletter_group_id VARCHAR(64) REFERENCES newsletter_groups(id),
    account_group_id VARCHAR(64) NOT NULL REFERENCES account_groups(id),
    status VARCHAR(24) NOT NULL DEFAULT 'DRAFT',
    concurrency_limit INTEGER NOT NULL DEFAULT 4,
    retry_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    auto_confirm BOOLEAN NOT NULL DEFAULT FALSE,
    created_by VARCHAR(96) NOT NULL DEFAULT 'admin_demo',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_newsletter_jobs_organization_id ON newsletter_jobs (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_jobs_newsletter_id ON newsletter_jobs (newsletter_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_jobs_newsletter_group_id ON newsletter_jobs (newsletter_group_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_jobs_account_group_id ON newsletter_jobs (account_group_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_jobs_status ON newsletter_jobs (status);

CREATE TABLE IF NOT EXISTS newsletter_tasks (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    job_id VARCHAR(64) NOT NULL REFERENCES newsletter_jobs(id),
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id),
    newsletter_id VARCHAR(64) NOT NULL REFERENCES newsletters(id),
    state VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    profile_version INTEGER,
    worker_id VARCHAR(96),
    browser_session_id VARCHAR(96),
    heartbeat_at TIMESTAMPTZ,
    lock_key VARCHAR(160),
    last_error VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_newsletter_task_job_account_newsletter UNIQUE (job_id, account_id, newsletter_id)
);

CREATE INDEX IF NOT EXISTS ix_newsletter_tasks_organization_id ON newsletter_tasks (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_tasks_job_id ON newsletter_tasks (job_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_tasks_account_id ON newsletter_tasks (account_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_tasks_newsletter_id ON newsletter_tasks (newsletter_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_tasks_state ON newsletter_tasks (state);
CREATE INDEX IF NOT EXISTS ix_newsletter_tasks_heartbeat ON newsletter_tasks (heartbeat_at);

CREATE TABLE IF NOT EXISTS newsletter_task_events (
    id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    task_id VARCHAR(64) NOT NULL REFERENCES newsletter_tasks(id),
    event VARCHAR(64) NOT NULL,
    result VARCHAR(32) NOT NULL DEFAULT 'success',
    detail VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_newsletter_task_events_organization_id ON newsletter_task_events (organization_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_task_events_task_id ON newsletter_task_events (task_id);
CREATE INDEX IF NOT EXISTS ix_newsletter_task_events_event ON newsletter_task_events (event);
CREATE INDEX IF NOT EXISTS ix_newsletter_task_events_created_at ON newsletter_task_events (created_at);