"""v4 projects: projects, project_items (BOM), events, attachments

Revision ID: f5a7c1e3b9d2
Revises: e5b8c3a91f47
Create Date: 2026-10-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5a7c1e3b9d2"
down_revision: str | Sequence[str] | None = "a4c6e8b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'planning'"), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("cover_model_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "tags",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("is_template", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("completed_at", _TS, nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cover_model_id"], ["models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_created_by_user_id", "projects", ["created_by_user_id"])

    op.create_table(
        "project_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("model_id", sa.BigInteger(), nullable=True),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("plate", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("acquired", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_items_project_id", "project_items", ["project_id", "position"])
    op.create_index("ix_project_items_model_id", "project_items", ["model_id"])

    op.create_table(
        "project_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_events_project_id", "project_events", ["project_id", "created_at"])

    op.create_table(
        "project_attachments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("stored_name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_attachments_project_id", "project_attachments", ["project_id"])

    op.add_column("print_archives", sa.Column("project_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_print_archives_project_id",
        "print_archives",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_print_archives_project_id", "print_archives", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_print_archives_project_id", table_name="print_archives")
    op.drop_constraint("fk_print_archives_project_id", "print_archives", type_="foreignkey")
    op.drop_column("print_archives", "project_id")
    op.drop_table("project_attachments")
    op.drop_table("project_events")
    op.drop_table("project_items")
    op.drop_table("projects")
