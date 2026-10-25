"""t8a per-user email notification preferences

Revision ID: e5b8c3a91f47
Revises: f2c8a4e6b1d9
Create Date: 2026-10-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b8c3a91f47"
down_revision: str | Sequence[str] | None = "f2c8a4e6b1d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FLAGS = (
    "notify_print_start",
    "notify_print_complete",
    "notify_print_failed",
    "notify_print_stopped",
)


def upgrade() -> None:
    op.create_table(
        "user_email_preferences",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        *[sa.Column(f, sa.Boolean(), server_default=sa.false(), nullable=False) for f in _FLAGS],
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_email_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_user_email_preferences_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_email_preferences")),
        sa.UniqueConstraint("user_id", name=op.f("uq_user_email_preferences_user_id")),
    )


def downgrade() -> None:
    op.drop_table("user_email_preferences")
