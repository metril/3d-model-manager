"""index print_queue (state, position)

Revision ID: c8f2a6d4e1b7
Revises: b7e1c4a9d2f6
Create Date: 2026-10-30 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8f2a6d4e1b7"
down_revision: str | Sequence[str] | None = "b7e1c4a9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_print_queue_state_position", "print_queue", ["state", "position"])


def downgrade() -> None:
    op.drop_index("ix_print_queue_state_position", table_name="print_queue")
