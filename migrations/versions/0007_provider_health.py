"""add persistent provider health and circuit breakers

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_health",
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("circuit_state", sa.String(length=20), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("total_successes", sa.Integer(), nullable=False),
        sa.Column("total_failures", sa.Integer(), nullable=False),
        sa.Column("failure_ewma", sa.Float(), nullable=False),
        sa.Column("latency_ewma_ms", sa.Float(), nullable=True),
        sa.Column("last_error_category", sa.String(length=50), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("model_id"),
    )
    op.create_index("ix_provider_health_provider", "provider_health", ["provider"])
    op.create_index(
        "ix_provider_health_circuit_state", "provider_health", ["circuit_state"]
    )
    op.create_index("ix_provider_health_updated_at", "provider_health", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_provider_health_updated_at", table_name="provider_health")
    op.drop_index("ix_provider_health_circuit_state", table_name="provider_health")
    op.drop_index("ix_provider_health_provider", table_name="provider_health")
    op.drop_table("provider_health")
