"""derivatives.version (GLB Y-up regeneration marker)

Revision ID: a1b2c3d4e5f6
Revises: e3f5a7b9c1d2
Create Date: 2026-11-04 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "e3f5a7b9c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "derivatives",
        sa.Column("version", sa.SmallInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("derivatives", "version")
