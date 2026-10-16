"""add live canary rollout tracking and guardrails

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inference_records") as batch_op:
        batch_op.add_column(
            sa.Column("policy_version_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("rollout_group", sa.String(length=20), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_inference_records_policy_version_id",
            "policy_records",
            ["policy_version_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_inference_records_policy_version_id", ["policy_version_id"]
        )
        batch_op.create_index("ix_inference_records_rollout_group", ["rollout_group"])

    op.add_column(
        "policy_records",
        sa.Column("canary_percent", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "policy_records",
        sa.Column(
            "guardrail_min_requests", sa.Integer(), nullable=False, server_default="20"
        ),
    )
    op.add_column(
        "policy_records",
        sa.Column("max_error_rate", sa.Float(), nullable=False, server_default="0.1"),
    )
    op.add_column(
        "policy_records",
        sa.Column(
            "max_latency_increase_percent",
            sa.Float(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column(
        "policy_records",
        sa.Column(
            "max_cost_increase_percent",
            sa.Float(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column(
        "policy_records",
        sa.Column("rollback_reason", sa.String(length=500), nullable=True),
    )

    op.create_table(
        "rollout_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("receipt_id", sa.String(length=64), nullable=True),
        sa.Column("rollout_group", sa.String(length=20), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["policy_records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rollout_events_policy_id", "rollout_events", ["policy_id"])
    op.create_index(
        "ix_rollout_events_rollout_group", "rollout_events", ["rollout_group"]
    )
    op.create_index("ix_rollout_events_created_at", "rollout_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_rollout_events_created_at", table_name="rollout_events")
    op.drop_index("ix_rollout_events_rollout_group", table_name="rollout_events")
    op.drop_index("ix_rollout_events_policy_id", table_name="rollout_events")
    op.drop_table("rollout_events")
    op.drop_column("policy_records", "rollback_reason")
    op.drop_column("policy_records", "max_cost_increase_percent")
    op.drop_column("policy_records", "max_latency_increase_percent")
    op.drop_column("policy_records", "max_error_rate")
    op.drop_column("policy_records", "guardrail_min_requests")
    op.drop_column("policy_records", "canary_percent")
    with op.batch_alter_table("inference_records") as batch_op:
        batch_op.drop_index("ix_inference_records_rollout_group")
        batch_op.drop_index("ix_inference_records_policy_version_id")
        batch_op.drop_constraint(
            "fk_inference_records_policy_version_id", type_="foreignkey"
        )
        batch_op.drop_column("rollout_group")
        batch_op.drop_column("policy_version_id")
