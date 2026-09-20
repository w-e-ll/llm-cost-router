import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData()
runs = sa.Table(
    "experiment_runs",
    metadata,
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
sa.Index("ix_runs_created_at", runs.c.created_at)
plans = sa.Table(
    "cost_plans",
    metadata,
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
sa.Index("ix_plans_source_run_id", plans.c.source_run_id)
