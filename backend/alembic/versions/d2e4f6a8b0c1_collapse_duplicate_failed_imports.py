"""collapse duplicate failed imports

Collection syncs used to create a new import row for every item whose earlier
import failed, piling up duplicates. Keep the newest failed row per
(site, external_id); it inherits ``dismissed_at`` if any duplicate had one.

Revision ID: d2e4f6a8b0c1
Revises: c7d1e3a5b9f2
Create Date: 2026-11-02 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2e4f6a8b0c1"
down_revision: str | Sequence[str] | None = "c7d1e3a5b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.services.import_dedup import collapse_duplicate_failed_imports

    collapse_duplicate_failed_imports(op.get_bind())


def downgrade() -> None:
    # Deleted duplicate failures are not recoverable (and not worth restoring).
    pass
