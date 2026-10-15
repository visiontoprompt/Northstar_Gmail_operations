"""Attribute sessions to operators for private browser desktops."""
from alembic import op
import sqlalchemy as sa


revision = "northstar_0003"
down_revision = "northstar_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "owner_employee_id" not in {item["name"] for item in sa.inspect(bind).get_columns("browser_sessions")}:
        op.add_column("browser_sessions", sa.Column("owner_employee_id", sa.String(length=96), nullable=True))
    op.create_index("ix_browser_sessions_owner_employee_id", "browser_sessions", ["owner_employee_id"], if_not_exists=True)
    # Historic sessions were created before ownership was tracked. Put those
    # records under the workspace administrator so they can safely close the
    # old shared-display windows; new sessions are attributed to their actor.
    bind.execute(sa.text(
        "UPDATE browser_sessions SET owner_employee_id = "
        "(SELECT employees.id FROM employees "
        "WHERE employees.organization_id = browser_sessions.organization_id "
        "AND employees.role = 'ADMINISTRATOR' ORDER BY employees.created_at LIMIT 1) "
        "WHERE browser_sessions.owner_employee_id IS NULL "
        "AND EXISTS (SELECT 1 FROM employees "
        "WHERE employees.organization_id = browser_sessions.organization_id "
        "AND employees.role = 'ADMINISTRATOR')"
    ))


def downgrade() -> None:
    op.drop_index("ix_browser_sessions_owner_employee_id", table_name="browser_sessions")
    op.drop_column("browser_sessions", "owner_employee_id")
