"""v4 slicer pipelines and cloud preset sync

Revision ID: d8e2a4c6f1b3
Revises: a9c3e5b7d1f4
Create Date: 2026-10-25 20:19:28.640013

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d8e2a4c6f1b3"
down_revision: str | Sequence[str] | None = "a9c3e5b7d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slicer_pipelines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("recipe_id", sa.BigInteger(), nullable=False),
        sa.Column("plate", sa.Integer(), nullable=True),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_slicer_pipelines_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["print_recipes.id"],
            name=op.f("fk_slicer_pipelines_recipe_id_print_recipes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slicer_pipelines")),
        sa.UniqueConstraint("name", name=op.f("uq_slicer_pipelines_name")),
    )
    op.create_index(
        op.f("ix_slicer_pipelines_created_by_user_id"),
        "slicer_pipelines",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_slicer_pipelines_recipe_id"), "slicer_pipelines", ["recipe_id"], unique=False
    )
    op.add_column("slice_jobs", sa.Column("pipeline_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "slice_jobs", sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.create_index(op.f("ix_slice_jobs_pipeline_id"), "slice_jobs", ["pipeline_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_slice_jobs_pipeline_id_slicer_pipelines"),
        "slice_jobs",
        "slicer_pipelines",
        ["pipeline_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "print_queue",
        sa.Column("slice_options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("slicer_profiles", sa.Column("cloud_source", sa.String(), nullable=True))
    op.add_column("slicer_profiles", sa.Column("cloud_id", sa.String(), nullable=True))
    op.add_column("slicer_profiles", sa.Column("cloud_hash", sa.String(), nullable=True))
    op.add_column("slicer_profiles", sa.Column("cloud_updated", sa.String(), nullable=True))
    op.add_column("slicer_profiles", sa.Column("data_hash", sa.String(), nullable=True))
    op.add_column(
        "slicer_profiles", sa.Column("cloud_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "uq_slicer_profiles_cloud",
        "slicer_profiles",
        ["cloud_source", "cloud_id"],
        unique=True,
        postgresql_where=sa.text("cloud_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_slicer_profiles_cloud",
        table_name="slicer_profiles",
        postgresql_where=sa.text("cloud_id IS NOT NULL"),
    )
    op.drop_column("print_queue", "slice_options")
    op.drop_column("slicer_profiles", "cloud_synced_at")
    op.drop_column("slicer_profiles", "data_hash")
    op.drop_column("slicer_profiles", "cloud_hash")
    op.drop_column("slicer_profiles", "cloud_updated")
    op.drop_column("slicer_profiles", "cloud_id")
    op.drop_column("slicer_profiles", "cloud_source")
    op.drop_constraint(
        op.f("fk_slice_jobs_pipeline_id_slicer_pipelines"), "slice_jobs", type_="foreignkey"
    )
    op.drop_index(op.f("ix_slice_jobs_pipeline_id"), table_name="slice_jobs")
    op.drop_column("slice_jobs", "options")
    op.drop_column("slice_jobs", "pipeline_id")
    op.drop_index(op.f("ix_slicer_pipelines_recipe_id"), table_name="slicer_pipelines")
    op.drop_index(op.f("ix_slicer_pipelines_created_by_user_id"), table_name="slicer_pipelines")
    op.drop_table("slicer_pipelines")
