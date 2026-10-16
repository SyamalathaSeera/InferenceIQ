"""Add optional LangSmith trace correlation.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inference_records",
        sa.Column("langsmith_trace_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_inference_records_langsmith_trace_id",
        "inference_records",
        ["langsmith_trace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inference_records_langsmith_trace_id",
        table_name="inference_records",
    )
    op.drop_column("inference_records", "langsmith_trace_id")
