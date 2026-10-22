"""add print_queue.slicing_started_at

Final-review fix wave I2: the dispatcher's `_reconcile` needs a timestamp to
tell a `slicing` entry that's merely in progress apart from one stuck there
well past SLICING_STALE_S (a worker crash/kill mid-slice) -- this column,
set when the entry's state becomes `slicing`.

Revision ID: f1a2b3c4d5e6
Revises: b4d7e2a9c6f1
Create Date: 2026-10-22 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "b4d7e2a9c6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "print_queue", sa.Column("slicing_started_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("print_queue", "slicing_started_at")
