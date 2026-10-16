"""add project PII policies and HMAC audit chains

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "pii_policy", sa.String(length=20), nullable=False, server_default="redact"
        ),
    )
    op.add_column(
        "projects",
        sa.Column("audit_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column("audit_chain_head", sa.String(length=64), nullable=True),
    )

    with op.batch_alter_table("inference_records") as batch_op:
        batch_op.add_column(
            sa.Column("pii_action", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "pii_detected_types",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "pii_redaction_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_hash"),
        sa.UniqueConstraint("project_id", "sequence"),
    )
    op.create_index("ix_audit_events_project_id", "audit_events", ["project_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_project_id", table_name="audit_events")
    op.drop_table("audit_events")
    with op.batch_alter_table("inference_records") as batch_op:
        batch_op.drop_column("pii_redaction_count")
        batch_op.drop_column("pii_detected_types")
        batch_op.drop_column("pii_action")
    op.drop_column("projects", "audit_chain_head")
    op.drop_column("projects", "audit_sequence")
    op.drop_column("projects", "pii_policy")
