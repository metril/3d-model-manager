"""v4 stock forecast + archive source attachments

Revision ID: a9c4e2f7b1d6
Revises: c5d7f9a1b3e8
Create Date: 2026-10-25 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c4e2f7b1d6"
down_revision: str | Sequence[str] | None = "c5d7f9a1b3e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_A = "print_archives"


def upgrade() -> None:
    for col in ("source_blob", "f3d_blob"):
        op.add_column(_A, sa.Column(col, sa.CHAR(64), nullable=True))
        op.create_foreign_key(
            f"fk_{_A}_{col}_blobs", _A, "blobs", [col], ["hash"], ondelete="SET NULL"
        )
    op.add_column(_A, sa.Column("source_name", sa.Text(), nullable=True))
    op.add_column(_A, sa.Column("f3d_name", sa.Text(), nullable=True))
    op.add_column(
        _A, sa.Column("source_linked", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.create_table(
        "stock_sku_settings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("material_type", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), server_default=sa.text("''"), nullable=False),
        sa.Column("color_key", sa.String(), server_default=sa.text("''"), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("safety_margin_value", sa.Float(), server_default=sa.text("14"), nullable=False),
        sa.Column(
            "safety_margin_unit", sa.String(), server_default=sa.text("'days'"), nullable=False
        ),
        sa.Column("alerts_snoozed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "material_type", "brand", "color_key", name="uq_stock_sku_settings_sku"
        ),
    )
    op.create_index(
        "ix_stock_sku_settings_created_by_user_id", "stock_sku_settings", ["created_by_user_id"]
    )


def downgrade() -> None:
    op.drop_table("stock_sku_settings")
    for col in ("source_blob", "f3d_blob"):
        op.drop_constraint(f"fk_{_A}_{col}_blobs", _A, type_="foreignkey")
        op.drop_column(_A, col)
    op.drop_column(_A, "source_name")
    op.drop_column(_A, "f3d_name")
    op.drop_column(_A, "source_linked")
