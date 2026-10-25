"""v4 plate-empty detection references

Revision ID: b7d1e3f5a902
Revises: a4c6e8b1d3f5
Create Date: 2026-10-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d1e3f5a902"
down_revision: str | Sequence[str] | None = "a4c6e8b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plate_references",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("plate_type", sa.String(64), server_default="", nullable=False),
        sa.Column("label", sa.String(100), server_default="", nullable=False),
        sa.Column("jpeg", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["printer_id"], ["printers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plate_references_printer_id", "plate_references", ["printer_id"])


def downgrade() -> None:
    op.drop_index("ix_plate_references_printer_id", table_name="plate_references")
    op.drop_table("plate_references")
