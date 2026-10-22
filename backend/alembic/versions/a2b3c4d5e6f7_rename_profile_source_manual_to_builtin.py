"""rename slicer_profiles.source "manual" -> "builtin"

Final-review fix wave M7: ``ProfileSource``'s hand-entered value is renamed
to ``builtin`` to match the spec's own wording (``import``/``builtin``).
``source`` is ``sa.Enum(..., native_enum=False, create_constraint=True)`` --
a plain VARCHAR plus a CHECK constraint -- so this is drop-and-recreate the
CHECK constraint plus a data backfill, no ``ALTER TYPE`` needed.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-10-22 00:00:02.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `op.f(...)` marks this as an ALREADY-final name -- `str_enum`/the raw
# migration enum both pass `name="profile_source"` directly as the CHECK
# constraint's own name (not run through the app's naming convention),
# mirroring `c69140b3976b_add_webp_blob_format.py`'s verified pattern.
_CONSTRAINT_NAME = "profile_source"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(_CONSTRAINT_NAME, "slicer_profiles", type_="check")
    # "builtin" (7 chars) is longer than "import"/"manual" (6) -- the
    # column was auto-sized to the enum's longest value (`native_enum=
    # False`'s behavior), so it must widen before the data backfill below.
    op.alter_column("slicer_profiles", "source", type_=sa.String(length=7))
    op.execute("UPDATE slicer_profiles SET source = 'builtin' WHERE source = 'manual'")
    op.create_check_constraint(
        _CONSTRAINT_NAME, "slicer_profiles", "source IN ('import', 'builtin')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_CONSTRAINT_NAME, "slicer_profiles", type_="check")
    op.execute("UPDATE slicer_profiles SET source = 'manual' WHERE source = 'builtin'")
    op.alter_column("slicer_profiles", "source", type_=sa.String(length=6))
    op.create_check_constraint(
        _CONSTRAINT_NAME, "slicer_profiles", "source IN ('import', 'manual')"
    )
