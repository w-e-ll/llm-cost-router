"""Create experiment runs and cost plans."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "experiment_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'aborted', 'failed')", name="ck_run_status"
        ),
    )
    op.create_index("ix_runs_created_at", "experiment_runs", ["created_at"])
    op.create_table(
        "cost_plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("settings", JSONB, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_plans_source_run_id", "cost_plans", ["source_run_id"])


def downgrade():
    op.drop_table("cost_plans")
    op.drop_table("experiment_runs")
