"""backfill smart_plugs.orphaned_at for plugs left by a printer delete

Revision ID: e3b7d1f5a9c2
Revises: d8e2a4c6f1b3
Create Date: 2026-10-26 09:00:00.000000

Plugs orphaned by a printer delete before ``orphaned_at`` existed have
``printer_id`` NULL and every automation flag switched off (the delete path
disables them all, incl. controls_printer_power). Standalone plugs existed
before the column too, so only rows matching that all-off signature are marked;
a deliberately standalone all-off accessory plug is thereby also gated behind
settings:write (the safe direction). Rows deleted before the disable-on-delete
behaviour kept their flags and are indistinguishable from standalone plugs, so
they are left alone.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e3b7d1f5a9c2"
down_revision: str | Sequence[str] | None = "d8e2a4c6f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE smart_plugs SET orphaned_at = now()
        WHERE printer_id IS NULL AND orphaned_at IS NULL
          AND schedule_enabled = false AND auto_off = false
          AND auto_on_before_print = false AND auto_off_after_drying = false
          AND controls_printer_power = false
        """
    )


def downgrade() -> None:
    pass
