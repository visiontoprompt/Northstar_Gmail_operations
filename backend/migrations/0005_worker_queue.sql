-- Redis-backed login worker queue: durable lease ledger and worker metadata.
-- Runtime startup uses SQLAlchemy metadata; this file documents the deployable migration.
-- Lease coordination itself lives in Redis (gmailops:v1), the job_locks table is the
-- durable audit/lease record used for stale-worker recovery.

CREATE TABLE IF NOT EXISTS job_locks (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
    account_id VARCHAR(64) NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    profile_id VARCHAR(64) REFERENCES browser_profiles(id),
    session_id VARCHAR(96),
    worker_id VARCHAR(160) NOT NULL,
    lock_key VARCHAR(180) NOT NULL,
    acquired_at DATETIME NOT NULL,
    heartbeat_at DATETIME,
    released_at DATETIME,
    released BOOLEAN NOT NULL DEFAULT 0,
    reason VARCHAR(120)
);

CREATE INDEX IF NOT EXISTS idx_job_locks_org_worker ON job_locks (organization_id, worker_id);
CREATE INDEX IF NOT EXISTS idx_job_locks_account_active ON job_locks (account_id, released);

ALTER TABLE login_job_items ADD COLUMN worker_id VARCHAR(160);
ALTER TABLE login_job_items ADD COLUMN heartbeat_at DATETIME;
ALTER TABLE login_job_items ADD COLUMN lock_key VARCHAR(180);
ALTER TABLE login_job_items ADD COLUMN paused_reason VARCHAR(500);
ALTER TABLE login_job_items ADD COLUMN completed_at DATETIME;

CREATE INDEX IF NOT EXISTS idx_login_job_items_worker_state ON login_job_items (worker_id, state);
CREATE INDEX IF NOT EXISTS idx_login_job_items_heartbeat ON login_job_items (heartbeat_at);