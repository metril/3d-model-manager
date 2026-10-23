"""q4 overlay tokens

Revision ID: b6d1e8a3c5f2
Revises: a8e2d4c6b1f3
Create Date: 2026-10-23 00:00:04.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6d1e8a3c5f2"
down_revision: str | Sequence[str] | None = "a8e2d4c6b1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "overlay_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("printer_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_overlay_tokens")),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_overlay_tokens_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_overlay_tokens_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_overlay_tokens_token_hash")),
    )
    op.create_index("ix_overlay_tokens_printer_id", "overlay_tokens", ["printer_id"])
    op.create_index("ix_overlay_tokens_created_by_user_id", "overlay_tokens", ["created_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_overlay_tokens_created_by_user_id", table_name="overlay_tokens")
    op.drop_index("ix_overlay_tokens_printer_id", table_name="overlay_tokens")
    op.drop_table("overlay_tokens")
