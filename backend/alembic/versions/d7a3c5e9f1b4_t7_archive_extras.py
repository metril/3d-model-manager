"""t7 archive extras

Revision ID: d7a3c5e9f1b4
Revises: c9d4e6f8b2a1
Create Date: 2026-10-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7a3c5e9f1b4"
down_revision: str | Sequence[str] | None = "c9d4e6f8b2a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    t = "print_archives"
    op.add_column(t, sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        t,
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.add_column(
        t,
        sa.Column(
            "result_override",
            _enum("archive_result_override", "completed", "failed", "canceled", "stopped"),
            nullable=True,
        ),
    )
    op.add_column(
        t,
        sa.Column("confirm_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(t, sa.Column("confirm_token", sa.Text(), nullable=True))
    op.add_column(t, sa.Column("confirm_token_used_at", sa.DateTime(timezone=True)))
    op.add_column(t, sa.Column("confirm_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        t,
        sa.Column("user_verdict", _enum("archive_user_verdict", "good", "bad"), nullable=True),
    )
    op.add_column(
        t,
        sa.Column(
            "user_verdict_source",
            _enum("archive_verdict_source", "dialog", "link", "plate_clear", "card", "api"),
            nullable=True,
        ),
    )
    op.add_column(t, sa.Column("verdict_at", sa.DateTime(timezone=True)))
    op.add_column(t, sa.Column("verdict_by_user_id", sa.BigInteger(), nullable=True))
    op.add_column(
        t,
        sa.Column(
            "photos",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_unique_constraint("uq_print_archives_confirm_token", t, ["confirm_token"])
    op.create_foreign_key(
        "fk_print_archives_verdict_by_user",
        t,
        "users",
        ["verdict_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    t = "print_archives"
    op.drop_constraint("fk_print_archives_verdict_by_user", t, type_="foreignkey")
    op.drop_constraint("uq_print_archives_confirm_token", t, type_="unique")
    for col in (
        "photos",
        "verdict_by_user_id",
        "verdict_at",
        "user_verdict_source",
        "user_verdict",
        "confirm_expires_at",
        "confirm_token_used_at",
        "confirm_token",
        "confirm_requested",
        "result_override",
        "tags",
        "notes",
    ):
        op.drop_column(t, col)
