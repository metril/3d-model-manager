"""print_jobs.print_error_at_send: the printer's print_error when the job was sent

Revision ID: a3b5c7d9e1f4
Revises: f2a4c6e8b0d3
"""

import sqlalchemy as sa

from alembic import op

revision = "a3b5c7d9e1f4"
down_revision = "f2a4c6e8b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("print_jobs", sa.Column("print_error_at_send", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("print_jobs", "print_error_at_send")
