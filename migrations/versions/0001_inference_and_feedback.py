"""Create inference and feedback records.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inference_records",
        sa.Column("receipt_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("requested_model", sa.String(length=255), nullable=True),
        sa.Column("selected_model", sa.String(length=255), nullable=False),
        sa.Column("selected_provider", sa.String(length=100), nullable=False),
        sa.Column("policy", sa.String(length=50), nullable=False),
        sa.Column("predicted_input_tokens", sa.Integer(), nullable=True),
        sa.Column("assumed_output_tokens", sa.Integer(), nullable=True),
        sa.Column("predicted_cost_usd", sa.Float(), nullable=True),
        sa.Column("actual_input_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_output_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_cost_usd", sa.Float(), nullable=True),
        sa.Column("provider_latency_ms", sa.Float(), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("fallbacks", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("receipt_id"),
    )
    op.create_index(
        "ix_inference_records_created_at", "inference_records", ["created_at"]
    )
    op.create_index("ix_inference_records_policy", "inference_records", ["policy"])
    op.create_index(
        "ix_inference_records_request_fingerprint",
        "inference_records",
        ["request_fingerprint"],
    )
    op.create_index(
        "ix_inference_records_selected_model",
        "inference_records",
        ["selected_model"],
    )
    op.create_index(
        "ix_inference_records_selected_provider",
        "inference_records",
        ["selected_provider"],
    )

    op.create_table(
        "feedback_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("receipt_id", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["inference_records.receipt_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feedback_records_receipt_id", "feedback_records", ["receipt_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_records_receipt_id", table_name="feedback_records")
    op.drop_table("feedback_records")
    op.drop_index(
        "ix_inference_records_selected_provider", table_name="inference_records"
    )
    op.drop_index("ix_inference_records_selected_model", table_name="inference_records")
    op.drop_index(
        "ix_inference_records_request_fingerprint", table_name="inference_records"
    )
    op.drop_index("ix_inference_records_policy", table_name="inference_records")
    op.drop_index("ix_inference_records_created_at", table_name="inference_records")
    op.drop_table("inference_records")
