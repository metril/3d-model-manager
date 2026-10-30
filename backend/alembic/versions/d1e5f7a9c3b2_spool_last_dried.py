"""spool last-dried stamp

Revision ID: d1e5f7a9c3b2
Revises: c8f2a6d4e1b7
Create Date: 2026-10-30 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e5f7a9c3b2"
down_revision: str | Sequence[str] | None = "c8f2a6d4e1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("spools", sa.Column("last_dried_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("spools", sa.Column("last_dried_temp_c", sa.Integer(), nullable=True))
    op.add_column("spools", sa.Column("last_dried_hours", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("spools", "last_dried_hours")
    op.drop_column("spools", "last_dried_temp_c")
    op.drop_column("spools", "last_dried_at")
