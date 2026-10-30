"""per-printer wear cost, archive wear_cost, sampled HA energy price

Revision ID: e2f6a8b1d4c5
Revises: d1e5f7a9c3b2
Create Date: 2026-10-30 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2f6a8b1d4c5"
down_revision: str | Sequence[str] | None = "d1e5f7a9c3b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("printers", sa.Column("wear_cost_per_hour", sa.Float(), nullable=True))
    op.add_column("prints", sa.Column("wear_cost", sa.Float(), nullable=True))
    op.add_column("print_jobs", sa.Column("energy_price_start", sa.Float(), nullable=True))
    op.add_column("print_jobs", sa.Column("energy_price_end", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("print_jobs", "energy_price_end")
    op.drop_column("print_jobs", "energy_price_start")
    op.drop_column("prints", "wear_cost")
    op.drop_column("printers", "wear_cost_per_hour")
