"""library trash: models.deleted_at / deleted_by_user_id

Revision ID: b7d3e1a9c4f2
Revises: b7d1e3f5a902
Create Date: 2026-10-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d3e1a9c4f2"
down_revision: str | Sequence[str] | None = "b7d1e3f5a902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("models", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("models", sa.Column("deleted_by_user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_models_deleted_by_user_id_users",
        "models",
        "users",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_models_deleted_at", "models", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_models_deleted_at", table_name="models")
    op.drop_constraint("fk_models_deleted_by_user_id_users", "models", type_="foreignkey")
    op.drop_column("models", "deleted_by_user_id")
    op.drop_column("models", "deleted_at")
