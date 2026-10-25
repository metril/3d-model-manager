"""s2 slice jobs + queue slice log

Revision ID: a4c6e8b1d3f5
Revises: e5b8c3a91f47
Create Date: 2026-10-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a4c6e8b1d3f5"
down_revision: str | Sequence[str] | None = "e5b8c3a91f47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("print_queue", sa.Column("last_log", sa.Text(), nullable=True))
    op.add_column("print_queue", sa.Column("slice_task_id", sa.String(), nullable=True))
    op.create_table(
        "slice_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("recipe_id", sa.BigInteger(), nullable=True),
        sa.Column("plate", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("celery_id", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log_tail", sa.Text(), nullable=True),
        sa.Column("sliced_file_id", sa.BigInteger(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["file_id"], ["files.id"], name=op.f("fk_slice_jobs_file_id_files"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["print_recipes.id"],
            name=op.f("fk_slice_jobs_recipe_id_print_recipes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sliced_file_id"],
            ["files.id"],
            name=op.f("fk_slice_jobs_sliced_file_id_files"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_slice_jobs_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slice_jobs")),
    )
    op.create_index(op.f("ix_slice_jobs_file_id"), "slice_jobs", ["file_id"])
    op.create_index(op.f("ix_slice_jobs_created_by_user_id"), "slice_jobs", ["created_by_user_id"])


def downgrade() -> None:
    op.drop_table("slice_jobs")
    op.drop_column("print_queue", "slice_task_id")
    op.drop_column("print_queue", "last_log")
