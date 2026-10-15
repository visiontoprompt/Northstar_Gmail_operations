-- Phase 11 production hardening indexes.
-- These indexes keep tenant-scoped health, queue recovery, and audit queries
-- bounded as the newsletter registry grows.

CREATE INDEX IF NOT EXISTS ix_newsletters_org_status_group
    ON newsletters (organization_id, status, group_id);

CREATE INDEX IF NOT EXISTS ix_newsletter_profiles_org_newsletter_status
    ON newsletter_automation_profiles (organization_id, newsletter_id, status, version);

CREATE INDEX IF NOT EXISTS ix_newsletter_tasks_org_job_state_heartbeat
    ON newsletter_tasks (organization_id, job_id, state, heartbeat_at);

CREATE INDEX IF NOT EXISTS ix_newsletter_task_events_org_task_created
    ON newsletter_task_events (organization_id, task_id, created_at);

CREATE INDEX IF NOT EXISTS ix_audit_events_org_action_target_created
    ON audit_events (organization_id, action, target_id, created_at);
