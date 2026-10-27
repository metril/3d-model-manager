"""Per-entity tag join tables (pure association tables, Core ``Table``).

``model_tags`` lives in ``app.models.library``. File tags key on
``(model_id, rel_path)`` rather than a ``files`` row so they survive new
revisions of the same path.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, ForeignKey, Index, Table, Text

from app.models.base import Base


def _join(name: str, col: str, parent: str) -> Table:
    table = Table(
        name,
        Base.metadata,
        Column(col, BigInteger, ForeignKey(f"{parent}.id", ondelete="CASCADE"), primary_key=True),
        Column("tag_id", BigInteger, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )
    Index(f"ix_{name}_tag_id", table.c.tag_id)
    return table


project_tags = _join("project_tags", "project_id", "projects")
project_attachment_tags = _join("project_attachment_tags", "attachment_id", "project_attachments")
print_archive_tags = _join("print_archive_tags", "archive_id", "print_archives")
followed_collection_tags = _join(
    "followed_collection_tags", "collection_id", "followed_collections"
)

model_file_tags = Table(
    "model_file_tags",
    Base.metadata,
    Column("model_id", BigInteger, ForeignKey("models.id", ondelete="CASCADE"), primary_key=True),
    Column("rel_path", Text, primary_key=True),
    Column("tag_id", BigInteger, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)
Index("ix_model_file_tags_tag_id", model_file_tags.c.tag_id)
