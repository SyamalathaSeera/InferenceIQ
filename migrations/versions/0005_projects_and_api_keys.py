"""add projects, API keys, budgets, and rate-limit admissions

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=False),
        sa.Column("monthly_budget_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_projects_name", "projects", ["name"])
    op.create_index("ix_projects_enabled", "projects", ["enabled"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=20), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])
    op.create_index("ix_api_keys_revoked_at", "api_keys", ["revoked_at"])

    op.create_table(
        "request_admissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_request_admissions_project_id", "request_admissions", ["project_id"]
    )
    op.create_index(
        "ix_request_admissions_created_at", "request_admissions", ["created_at"]
    )

    with op.batch_alter_table("inference_records") as batch_op:
        batch_op.add_column(
            sa.Column("project_id", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_inference_records_project_id",
            "projects",
            ["project_id"],
            ["id"],
        )
        batch_op.create_index("ix_inference_records_project_id", ["project_id"])


def downgrade() -> None:
    with op.batch_alter_table("inference_records") as batch_op:
        batch_op.drop_index("ix_inference_records_project_id")
        batch_op.drop_constraint("fk_inference_records_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")
    op.drop_index("ix_request_admissions_created_at", table_name="request_admissions")
    op.drop_index("ix_request_admissions_project_id", table_name="request_admissions")
    op.drop_table("request_admissions")
    op.drop_index("ix_api_keys_revoked_at", table_name="api_keys")
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_project_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_projects_enabled", table_name="projects")
    op.drop_index("ix_projects_name", table_name="projects")
    op.drop_table("projects")
