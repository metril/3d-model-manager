"""print_jobs.paused_at: when the job last entered PAUSED

Revision ID: b4c6d8e0f2a5
Revises: a3b5c7d9e1f4
"""

import sqlalchemy as sa

from alembic import op

revision = "b4c6d8e0f2a5"
down_revision = "a3b5c7d9e1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("print_jobs", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("print_jobs", "paused_at")
