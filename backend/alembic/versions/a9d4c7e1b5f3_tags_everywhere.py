"""tags everywhere: hex colors, icon/description, per-entity join tables

Tags become a shared vocabulary: ``tags.color`` moves from a 10-key palette to a
``#rrggbb`` hex (existing keys data-migrated), tags gain ``icon`` (lucide name)
and ``description``, names become case-insensitively unique, and every taggable
entity gets its own join table (FK ON DELETE CASCADE + tag_id index).
``model_file_tags`` keys on (model_id, rel_path) so file tags survive revisions.

Revision ID: a9d4c7e1b5f3
Revises: e3b7d1f5a9c2
Create Date: 2026-10-27 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9d4c7e1b5f3"
down_revision: str | Sequence[str] | None = "e3b7d1f5a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PALETTE_HEX = {
    "slate": "#64748b",
    "red": "#ef4444",
    "orange": "#f97316",
    "amber": "#f59e0b",
    "green": "#22c55e",
    "teal": "#14b8a6",
    "blue": "#3b82f6",
    "indigo": "#6366f1",
    "violet": "#8b5cf6",
    "pink": "#ec4899",
}

# (join table, entity column, entity table); every entity PK is BigInteger.
_SIMPLE_JOINS = (
    ("project_tags", "project_id", "projects"),
    ("project_attachment_tags", "attachment_id", "project_attachments"),
    ("print_archive_tags", "archive_id", "print_archives"),
    ("followed_collection_tags", "collection_id", "followed_collections"),
)


def _tag_fk() -> sa.Column:
    return sa.Column(
        "tag_id",
        sa.BigInteger(),
        sa.ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade() -> None:
    op.drop_constraint("ck_tags_color_palette", "tags", type_="check")
    op.alter_column("tags", "color", type_=sa.String(7), existing_nullable=True)
    for key, hex_ in _PALETTE_HEX.items():
        op.execute(sa.text("UPDATE tags SET color = :h WHERE color = :k").bindparams(h=hex_, k=key))
    op.execute("UPDATE tags SET color = NULL WHERE color !~ '^#[0-9a-fA-F]{6}$'")
    op.add_column("tags", sa.Column("icon", sa.String(), nullable=True))
    op.add_column("tags", sa.Column("description", sa.Text(), nullable=True))

    # Merge pre-existing case-duplicates (lowest id wins) before the unique index.
    op.execute(
        """
        INSERT INTO model_tags (model_id, tag_id)
        SELECT mt.model_id, k.keep_id FROM model_tags mt
        JOIN tags t ON t.id = mt.tag_id
        JOIN (SELECT lower(name) AS n, min(id) AS keep_id FROM tags GROUP BY lower(name)) k
          ON k.n = lower(t.name) AND k.keep_id <> t.id
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        DELETE FROM tags t USING
          (SELECT lower(name) AS n, min(id) AS keep_id FROM tags GROUP BY lower(name)) k
        WHERE k.n = lower(t.name) AND k.keep_id <> t.id
        """
    )
    op.create_index("uq_tags_name_lower", "tags", [sa.text("lower(name)")], unique=True)

    op.create_table(
        "model_file_tags",
        sa.Column(
            "model_id",
            sa.BigInteger(),
            sa.ForeignKey("models.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rel_path", sa.Text(), primary_key=True),
        _tag_fk(),
    )
    op.create_index("ix_model_file_tags_tag_id", "model_file_tags", ["tag_id"])
    for table, col, parent in _SIMPLE_JOINS:
        op.create_table(
            table,
            sa.Column(
                col,
                sa.BigInteger(),
                sa.ForeignKey(f"{parent}.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            _tag_fk(),
        )
        op.create_index(f"ix_{table}_tag_id", table, ["tag_id"])


def downgrade() -> None:
    for table, _col, _parent in reversed(_SIMPLE_JOINS):
        op.drop_table(table)
    op.drop_table("model_file_tags")
    op.drop_index("uq_tags_name_lower", table_name="tags")
    op.drop_column("tags", "description")
    op.drop_column("tags", "icon")
    for key, hex_ in _PALETTE_HEX.items():
        op.execute(
            sa.text("UPDATE tags SET color = :k WHERE lower(color) = :h").bindparams(k=key, h=hex_)
        )
    keys = ",".join(f"'{k}'" for k in _PALETTE_HEX)
    op.execute(f"UPDATE tags SET color = NULL WHERE color NOT IN ({keys})")
    op.alter_column("tags", "color", type_=sa.String(), existing_nullable=True)
    op.create_check_constraint(
        "ck_tags_color_palette",
        "tags",
        sa.or_(sa.column("color").in_(tuple(_PALETTE_HEX)), sa.column("color").is_(None)),
    )
