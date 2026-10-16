"""add versioned policy workflows

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy", sa.String(length=50), nullable=False),
        sa.Column("require_zero_data_retention", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("sample_limit", sa.Integer(), nullable=False),
        sa.Column("simulation", sa.JSON(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version"),
    )
    op.create_index("ix_policy_records_name", "policy_records", ["name"])
    op.create_index("ix_policy_records_status", "policy_records", ["status"])
    op.create_index("ix_policy_records_created_at", "policy_records", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_policy_records_created_at", table_name="policy_records")
    op.drop_index("ix_policy_records_status", table_name="policy_records")
    op.drop_index("ix_policy_records_name", table_name="policy_records")
    op.drop_table("policy_records")
