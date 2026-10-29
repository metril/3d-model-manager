"""profile scope metadata + printers.nozzle_mm

Derived scope columns on slicer_profiles (filled by the app at import/sync/
PATCH and a startup backfill) and the last-seen nozzle on printers.

Revision ID: b7e1c4a9d2f6
Revises: a9d3e5f7b1c2
Create Date: 2026-10-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7e1c4a9d2f6"
down_revision: str | Sequence[str] | None = "a9d3e5f7b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("slicer_profiles", sa.Column("printer_model_key", sa.String(), nullable=True))
    op.add_column(
        "slicer_profiles",
        sa.Column("compat", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("slicer_profiles", sa.Column("layer_height_mm", sa.Float(), nullable=True))
    op.create_index(
        op.f("ix_slicer_profiles_printer_model_key"), "slicer_profiles", ["printer_model_key"]
    )
    op.add_column("printers", sa.Column("nozzle_mm", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("printers", "nozzle_mm")
    op.drop_index(op.f("ix_slicer_profiles_printer_model_key"), table_name="slicer_profiles")
    op.drop_column("slicer_profiles", "layer_height_mm")
    op.drop_column("slicer_profiles", "compat")
    op.drop_column("slicer_profiles", "printer_model_key")
