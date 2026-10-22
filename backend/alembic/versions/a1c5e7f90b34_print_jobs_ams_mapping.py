"""print_jobs.ams_mapping

Revision ID: a1c5e7f90b34
Revises: d8b3f2a6c4e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1c5e7f90b34"
down_revision: str | Sequence[str] | None = "d8b3f2a6c4e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("print_jobs", sa.Column("ams_mapping", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("print_jobs", "ams_mapping")
