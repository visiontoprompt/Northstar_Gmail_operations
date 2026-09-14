-- Phase 2 hand-off boundary: durable profile reservation and human login sessions.
-- Credential material is intentionally absent from these tables.

CREATE TABLE IF NOT EXISTS browser_profiles (
  id VARCHAR(96) PRIMARY KEY,
  organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
  account_id VARCHAR(64) NOT NULL UNIQUE REFERENCES gmail_accounts(id) ON DELETE CASCADE,
  storage_reference VARCHAR(500) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'RESERVED',
  lock_state VARCHAR(32) NOT NULL DEFAULT 'UNLOCKED',
  active_session_id VARCHAR(96),
  last_opened_at TIMESTAMPTZ,
  last_closed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS browser_sessions (
  id VARCHAR(96) PRIMARY KEY,
  organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
  account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
  profile_id VARCHAR(96) NOT NULL REFERENCES browser_profiles(id),
  state VARCHAR(32) NOT NULL DEFAULT 'STARTING',
  login_state VARCHAR(40) NOT NULL DEFAULT 'REQUIRED',
  runtime_type VARCHAR(32) NOT NULL DEFAULT 'LOCAL_HANDOFF',
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at TIMESTAMPTZ,
  failure_reason VARCHAR(500),
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_browser_sessions_org_state ON browser_sessions(organization_id, state);
CREATE INDEX IF NOT EXISTS ix_browser_sessions_account ON browser_sessions(account_id, created_at DESC);
