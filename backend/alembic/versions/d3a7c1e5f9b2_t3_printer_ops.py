"""t3 printer ops: awaiting_plate_clear + ams_labels

Revision ID: d3a7c1e5f9b2
Revises: c9d4e6f8b2a1
Create Date: 2026-10-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3a7c1e5f9b2"
down_revision: str | Sequence[str] | None = "c9d4e6f8b2a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "printers",
        sa.Column("awaiting_plate_clear", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "ams_labels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ams_serial", sa.String(), nullable=False),
        sa.Column("ams_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ams_labels_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ams_labels")),
        sa.UniqueConstraint("ams_serial", name=op.f("uq_ams_labels_ams_serial")),
    )


def downgrade() -> None:
    op.drop_table("ams_labels")
    op.drop_column("printers", "awaiting_plate_clear")
