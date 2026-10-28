"""imports.extra_tags: tag names applied when an extension import completes

Revision ID: b3e8a1c5d7f2
Revises: a9d4c7e1b5f3
Create Date: 2026-10-28 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b3e8a1c5d7f2"
down_revision: str | Sequence[str] | None = "a9d4c7e1b5f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("imports", sa.Column("extra_tags", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("imports", "extra_tags")
