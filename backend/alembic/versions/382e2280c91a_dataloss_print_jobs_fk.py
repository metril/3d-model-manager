"""print_jobs.file_id ON DELETE SET NULL + (printer_id, state) index

Revision ID: 382e2280c91a
Revises: b8e4c2d6f0a1
Create Date: 2026-11-06 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "382e2280c91a"
down_revision: str | Sequence[str] | None = "b8e4c2d6f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_print_jobs_file_id_files", "print_jobs", type_="foreignkey")
    op.create_foreign_key(
        "fk_print_jobs_file_id_files",
        "print_jobs",
        "files",
        ["file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "pending_storage_deletes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("backend_id", sa.BigInteger(), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_index("ix_pending_storage_deletes_key", "pending_storage_deletes", ["key"])
    op.create_index("ix_print_jobs_printer_state", "print_jobs", ["printer_id", "state"])


def downgrade() -> None:
    op.drop_index("ix_print_jobs_printer_state", table_name="print_jobs")
    op.drop_table("pending_storage_deletes")
    op.drop_constraint("fk_print_jobs_file_id_files", "print_jobs", type_="foreignkey")
    op.create_foreign_key("fk_print_jobs_file_id_files", "print_jobs", "files", ["file_id"], ["id"])
