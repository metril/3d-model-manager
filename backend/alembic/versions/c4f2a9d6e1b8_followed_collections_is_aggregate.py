"""followed_collections.is_aggregate: flag the uid "All collected models" list

Revision ID: c4f2a9d6e1b8
Revises: b3e8a1c5d7f2
Create Date: 2026-10-28 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4f2a9d6e1b8"
down_revision: str | Sequence[str] | None = "b3e8a1c5d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "followed_collections",
        sa.Column("is_aggregate", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        "UPDATE followed_collections SET is_aggregate = true "
        "WHERE site = 'makerworld' AND kind = 'collection' AND title = 'All collected models'"
    )
    op.create_table(
        "collection_autofollow_dismissed",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("site", sa.String(), nullable=False),
        sa.Column("list_id", sa.Text(), nullable=False),
        sa.UniqueConstraint("site", "list_id", name="uq_collection_autofollow_dismissed_site_list"),
    )


def downgrade() -> None:
    op.drop_table("collection_autofollow_dismissed")
    op.drop_column("followed_collections", "is_aggregate")
