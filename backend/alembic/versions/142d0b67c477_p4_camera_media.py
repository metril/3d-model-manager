"""p4 camera media: video blob kind, mp4/avi formats, archive media_note

`BlobKind.VIDEO` + `BlobFormat.{mp4,avi}` (P4 camera plan Task 6) back
`PrintArchive.snapshot_blob`/`timelapse_blob` (already columns as of P2's
`c5e2a8d4b7f1`): the snapshot is a `jpg` (existing `BlobFormat.JPG`), the
timelapse is an `mp4`/`avi` video, both stored as content-addressed blobs
like every other archive artifact. Same drop/recreate CHECK pattern as
`f3a1c9d2e8b7`/`c69140b3976b` (`sa.Enum(..., native_enum=False,
create_constraint=True)` -- a VARCHAR plus a CHECK constraint, so widening
is drop-and-recreate the CHECK, no column-type change). `format` is already
sized off `"gcode_3mf"` (9 chars, > `"avi"`'s 3); `kind` off `"sliced"` (6
chars, > `"video"`'s 5) -- neither column needs widening.

`print_archives.media_note` is a free-text field for a human note about the
captured media (e.g. "camera disconnected mid-print").

Revision ID: 142d0b67c477
Revises: a1c5e7f90b34
Create Date: 2026-10-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "142d0b67c477"
down_revision: str | Sequence[str] | None = "a1c5e7f90b34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_CONSTRAINT = "ck_blobs_blob_kind"
_FORMAT_CONSTRAINT = "ck_blobs_blob_format"
_OLD_KINDS = ("mesh", "cad", "sliced", "gcode", "image", "doc", "other")
_NEW_KINDS = (*_OLD_KINDS[:-1], "video", "other")
_OLD_FORMATS = (
    "stl",
    "3mf",
    "obj",
    "step",
    "iges",
    "gcode_3mf",
    "gcode",
    "png",
    "jpg",
    "webp",
    "pdf",
    "md",
    "txt",
    "docx",
    "other",
)
_NEW_FORMATS = (*_OLD_FORMATS[:-1], "mp4", "avi", "other")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("print_archives", sa.Column("media_note", sa.Text(), nullable=True))

    op.drop_constraint(op.f(_KIND_CONSTRAINT), "blobs", type_="check")
    op.create_check_constraint(op.f(_KIND_CONSTRAINT), "blobs", _in_clause("kind", _NEW_KINDS))
    op.drop_constraint(op.f(_FORMAT_CONSTRAINT), "blobs", type_="check")
    op.create_check_constraint(
        op.f(_FORMAT_CONSTRAINT), "blobs", _in_clause("format", _NEW_FORMATS)
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE blobs SET kind = 'other' WHERE kind = 'video'"))
    bind.execute(sa.text("UPDATE blobs SET format = 'other' WHERE format IN ('mp4', 'avi')"))

    op.drop_constraint(op.f(_FORMAT_CONSTRAINT), "blobs", type_="check")
    op.create_check_constraint(
        op.f(_FORMAT_CONSTRAINT), "blobs", _in_clause("format", _OLD_FORMATS)
    )
    op.drop_constraint(op.f(_KIND_CONSTRAINT), "blobs", type_="check")
    op.create_check_constraint(op.f(_KIND_CONSTRAINT), "blobs", _in_clause("kind", _OLD_KINDS))

    op.drop_column("print_archives", "media_note")
