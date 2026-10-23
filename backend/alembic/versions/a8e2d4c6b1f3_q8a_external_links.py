"""q8a external sidebar links

Revision ID: a8e2d4c6b1f3
Revises: a7c3e9d1b5f2
Create Date: 2026-10-23 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8e2d4c6b1f3"
down_revision: str | Sequence[str] | None = "a7c3e9d1b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_links",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("open_mode", sa.Text(), server_default="iframe", nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_links")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_external_links_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "open_mode IN ('iframe', 'newtab')", name=op.f("ck_external_links_open_mode")
        ),
    )
    op.create_index(
        "ix_external_links_created_by_user_id", "external_links", ["created_by_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_external_links_created_by_user_id", table_name="external_links")
    op.drop_table("external_links")
