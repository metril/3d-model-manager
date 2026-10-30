"""layer timelapse: print_archives.timelapse_frames/timelapse_layers

Revision ID: b5c9d2e7f3a4
Revises: f3a7b9c2d5e1
Create Date: 2026-10-30 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5c9d2e7f3a4"
down_revision: str | Sequence[str] | None = "f3a7b9c2d5e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("print_archives", sa.Column("timelapse_frames", sa.Integer(), nullable=True))
    op.add_column("print_archives", sa.Column("timelapse_layers", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("print_archives", "timelapse_layers")
    op.drop_column("print_archives", "timelapse_frames")
