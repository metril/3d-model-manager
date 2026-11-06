"""print_archives: partial indexes on blob columns + GIN on photos

``release_blobs`` checks every archive blob column (and ``photos``) for
references; without these each check was a sequential scan.

Revision ID: d9e4b7a1c3f2
Revises: c7d3a91e5b48
Create Date: 2026-11-06 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9e4b7a1c3f2"
down_revision: str | Sequence[str] | None = "c7d3a91e5b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "archive_blob",
    "thumbnail_blob",
    "snapshot_blob",
    "timelapse_blob",
    "source_blob",
    "f3d_blob",
)


def upgrade() -> None:
    # CONCURRENTLY: no write lock on print_archives while indexing.
    with op.get_context().autocommit_block():
        for col in _COLS:
            op.create_index(
                f"ix_print_archives_{col}",
                "print_archives",
                [col],
                postgresql_where=sa.text(f"{col} IS NOT NULL"),
                postgresql_concurrently=True,
                if_not_exists=True,
            )
        op.create_index(
            "ix_print_archives_photos",
            "print_archives",
            ["photos"],
            postgresql_using="gin",
            postgresql_ops={"photos": "jsonb_path_ops"},
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_print_archives_photos",
            table_name="print_archives",
            postgresql_concurrently=True,
            if_exists=True,
        )
        for col in reversed(_COLS):
            op.drop_index(
                f"ix_print_archives_{col}",
                table_name="print_archives",
                postgresql_concurrently=True,
                if_exists=True,
            )
