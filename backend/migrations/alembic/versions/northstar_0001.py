"""Initialize/bring forward the current Northstar relational schema.

Existing deployments are upgraded idempotently from the ORM metadata, then
known legacy columns are reconciled. New schema changes should use subsequent
Alembic revisions rather than runtime ALTER TABLE checks.
"""
from alembic import op

from app.db import Base, _ensure_account_tags_column, _ensure_login_job_operation_columns, _ensure_newsletter_signup_password_column, _ensure_newsletter_skill_columns, _ensure_profile_authenticated_column, _ensure_proxy_group_column, _ensure_proxy_inventory_payload_column, _ensure_workspace_settings_agent_columns
import app.models  # noqa: F401

revision = "northstar_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    _ensure_profile_authenticated_column()
    _ensure_proxy_inventory_payload_column()
    _ensure_proxy_group_column()
    _ensure_account_tags_column()
    _ensure_workspace_settings_agent_columns()
    _ensure_newsletter_skill_columns()
    _ensure_login_job_operation_columns()
    _ensure_newsletter_signup_password_column()


def downgrade() -> None:
    raise RuntimeError("The consolidated production baseline is intentionally irreversible; restore from a database backup instead.")
