"""smart_plugs.orphaned_at

Revision ID: a9c3e5b7d1f4
Revises: a9c4e2f7b1d6
Create Date: 2026-10-25 15:00:00.000000

Set when a plug is left behind by a printer delete; control of such a plug
needs settings:write until it is re-linked.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c3e5b7d1f4"
down_revision: str | Sequence[str] | None = "a9c4e2f7b1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "smart_plugs", sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("smart_plugs", "orphaned_at")
