"""printers.failed_at / plate_cleared_at: a FAILED printer needs a person's ack

Revision ID: e1f3a5b7c9d2
Revises: d9e4b7a1c3f2
"""

import sqlalchemy as sa

from alembic import op

revision = "e1f3a5b7c9d2"
down_revision = "d9e4b7a1c3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("printers", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "printers", sa.Column("plate_cleared_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.add_column("print_jobs", sa.Column("starting_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("print_jobs", "starting_at")
    op.drop_column("printers", "plate_cleared_at")
    op.drop_column("printers", "failed_at")
