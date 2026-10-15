"""Persist OVH Floating IP proxy recovery progress and saved bindings."""
from alembic import op
import sqlalchemy as sa

revision = "northstar_0002"
down_revision = "northstar_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # The consolidated northstar_0001 baseline creates all current ORM tables
    # on a fresh install. Existing deployments already at 0001 still need this
    # table, so make the forward revision safe for both paths.
    if sa.inspect(bind).has_table("ovh_proxy_recovery_jobs"):
        return
    op.create_table(
        "ovh_proxy_recovery_jobs",
        sa.Column("id", sa.String(length=96), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_key", sa.String(length=256), nullable=False),
        sa.Column("lock_key", sa.String(length=256), nullable=True),
        sa.Column("service_name", sa.String(length=128), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "lock_key", name="uq_ovh_proxy_recovery_active"),
    )
    op.create_index("ix_ovh_proxy_recovery_jobs_organization_id", "ovh_proxy_recovery_jobs", ["organization_id"])
    op.create_index("ix_ovh_proxy_recovery_jobs_resource_key", "ovh_proxy_recovery_jobs", ["resource_key"])
    op.create_index("ix_ovh_proxy_recovery_jobs_status", "ovh_proxy_recovery_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ovh_proxy_recovery_jobs_status", table_name="ovh_proxy_recovery_jobs")
    op.drop_index("ix_ovh_proxy_recovery_jobs_resource_key", table_name="ovh_proxy_recovery_jobs")
    op.drop_index("ix_ovh_proxy_recovery_jobs_organization_id", table_name="ovh_proxy_recovery_jobs")
    op.drop_table("ovh_proxy_recovery_jobs")
