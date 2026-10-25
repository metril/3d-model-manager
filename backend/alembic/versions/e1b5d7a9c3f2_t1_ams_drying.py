"""t1 ams drying: scheduled_dryings + smart_plugs.auto_off_after_drying

Revision ID: e1b5d7a9c3f2
Revises: d2e7a1c5b903
Create Date: 2026-10-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1b5d7a9c3f2"
down_revision: str | Sequence[str] | None = "d2e7a1c5b903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "smart_plugs",
        sa.Column("auto_off_after_drying", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "scheduled_dryings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("ams_id", sa.Integer(), nullable=False),
        sa.Column("temp", sa.Integer(), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("filament", sa.Text(), server_default="", nullable=False),
        sa.Column("rotate_tray", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("start_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("waiting_reason", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.Text(), server_default="manual", nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_scheduled_dryings_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_scheduled_dryings_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_dryings")),
    )
    op.create_index(
        op.f("ix_scheduled_dryings_printer_id"), "scheduled_dryings", ["printer_id"], unique=False
    )
    op.create_index(
        op.f("ix_scheduled_dryings_created_by_user_id"),
        "scheduled_dryings",
        ["created_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("scheduled_dryings")
    op.drop_column("smart_plugs", "auto_off_after_drying")
