"""q10 failure_detections

Revision ID: c9d4e6f8b2a1
Revises: b6d1e8a3c5f2
Create Date: 2026-10-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d4e6f8b2a1"
down_revision: str | Sequence[str] | None = "b6d1e8a3c5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "failure_detections",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("print_job_id", sa.BigInteger(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("smoothed", sa.Float(), nullable=False),
        sa.Column("frame_blob_key", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_failure_detections_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["print_job_id"],
            ["print_jobs.id"],
            name=op.f("fk_failure_detections_print_job_id_print_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_failure_detections_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_failure_detections")),
    )
    op.create_index(
        "ix_failure_detections_printer_id_created_at",
        "failure_detections",
        ["printer_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_failure_detections_printer_id_created_at", table_name="failure_detections")
    op.drop_table("failure_detections")
