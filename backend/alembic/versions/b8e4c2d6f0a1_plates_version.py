"""blob_meta.plates_version (re-analyse plates for filament data)

Revision ID: b8e4c2d6f0a1
Revises: a1b2c3d4e5f6
Create Date: 2026-11-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8e4c2d6f0a1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("blob_meta", sa.Column("plates_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("blob_meta", "plates_version")
