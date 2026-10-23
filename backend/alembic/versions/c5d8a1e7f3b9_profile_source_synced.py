"""allow slicer_profiles.source "synced"

Profiles pulled from the slicer sidecar's bundled Bambu presets are tagged
``synced`` so a re-sync can refresh them without ever overwriting an
imported, hand-made or user-edited row. ``source`` is a VARCHAR plus a CHECK
constraint (``native_enum=False``), so this just recreates the constraint.

Revision ID: c5d8a1e7f3b9
Revises: e8b3f1a6c2d4
Create Date: 2026-10-23 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d8a1e7f3b9"
down_revision: str | Sequence[str] | None = "e8b3f1a6c2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT_NAME = "profile_source"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(_CONSTRAINT_NAME, "slicer_profiles", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME, "slicer_profiles", "source IN ('import', 'builtin', 'synced')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_CONSTRAINT_NAME, "slicer_profiles", type_="check")
    op.execute("UPDATE slicer_profiles SET source = 'import' WHERE source = 'synced'")
    op.create_check_constraint(
        _CONSTRAINT_NAME, "slicer_profiles", "source IN ('import', 'builtin')"
    )
