"""blob_meta.plates_json + files.source_meta

Revision ID: e3f5a7b9c1d2
Revises: d2e4f6a8b0c1
Create Date: 2026-11-03 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3f5a7b9c1d2"
down_revision: str | Sequence[str] | None = "d2e4f6a8b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("blob_meta", sa.Column("plates_json", postgresql.JSONB(), nullable=True))
    op.add_column("files", sa.Column("source_meta", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("files", "source_meta")
    op.drop_column("blob_meta", "plates_json")
