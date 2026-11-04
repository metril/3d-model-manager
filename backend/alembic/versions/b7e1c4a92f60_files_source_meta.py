"""files.source_meta

Per-file import provenance (MakerWorld print-profile printers/plates).

Revision ID: b7e1c4a92f60
Revises: d2e4f6a8b0c1
Create Date: 2026-11-03 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e1c4a92f60"
down_revision: str | Sequence[str] | None = "d2e4f6a8b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("files", sa.Column("source_meta", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("files", "source_meta")
