"""add imports.dismissed_at

Lets a user acknowledge a failed import so it stops counting toward the
Collections nav badge.

Revision ID: a9d3e5f7b1c2
Revises: c4f2a9d6e1b8
Create Date: 2026-10-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9d3e5f7b1c2"
down_revision: str | Sequence[str] | None = "c4f2a9d6e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("imports", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("imports", "dismissed_at")
