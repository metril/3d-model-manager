"""print archive: archives table, external job origin, run cost columns

P2: new print_archives table; print_jobs gain origin/plate and a nullable
file_id (external jobs have no library file); prints gain print_job_id,
source, quality, failure_reason and cost columns, and a nullable model_id
(auto-archived runs may match no model). Downgrade deletes external jobs and
model-less prints so the original NOT NULL constraints can be restored.

Revision ID: d8b3f2a6c4e9
Revises: c5e2a8d4b7f1
Create Date: 2026-10-21 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d8b3f2a6c4e9"
down_revision = "c5e2a8d4b7f1"
branch_labels = None
depends_on = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.add_column(
        "print_jobs",
        sa.Column(
            "origin",
            _enum("print_job_origin", "app", "external"),
            server_default="app",
            nullable=False,
        ),
    )
    op.add_column("print_jobs", sa.Column("plate", sa.Integer(), nullable=True))
    op.alter_column("print_jobs", "file_id", existing_type=sa.BigInteger(), nullable=True)

    op.alter_column("prints", "model_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("prints", sa.Column("print_job_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_prints_print_job_id_print_jobs",
        "prints",
        "print_jobs",
        ["print_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_prints_print_job_id", "prints", ["print_job_id"])
    op.add_column(
        "prints",
        sa.Column(
            "source",
            _enum("print_source", "manual", "auto"),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "prints", sa.Column("quality", _enum("print_quality", "good", "reject"), nullable=True)
    )
    op.add_column("prints", sa.Column("failure_reason", sa.Text(), nullable=True))
    for col in ("filament_cost", "machine_cost", "energy_kwh", "energy_cost", "total_cost"):
        op.add_column("prints", sa.Column(col, sa.Float(), nullable=True))

    op.create_table(
        "print_archives",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("print_job_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=True),
        sa.Column("revision_id", sa.BigInteger(), nullable=True),
        sa.Column("archive_blob", sa.CHAR(64), nullable=True),
        sa.Column("thumbnail_blob", sa.CHAR(64), nullable=True),
        sa.Column("snapshot_blob", sa.CHAR(64), nullable=True),
        sa.Column("timelapse_blob", sa.CHAR(64), nullable=True),
        sa.Column(
            "result",
            _enum("archive_result", "completed", "failed", "canceled", "stopped"),
            nullable=False,
        ),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("est_filament", postgresql.JSONB(), nullable=True),
        sa.Column("plate", sa.Integer(), nullable=True),
        sa.Column("slicer_meta", postgresql.JSONB(), nullable=True),
        sa.Column(
            "fetch_state",
            _enum("archive_fetch_state", "pending", "done", "missing", "error"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_print_archives")),
        sa.UniqueConstraint("print_job_id", name=op.f("uq_print_archives_print_job_id")),
        sa.ForeignKeyConstraint(
            ["print_job_id"],
            ["print_jobs.id"],
            name=op.f("fk_print_archives_print_job_id_print_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_print_archives_model_id_models"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["revisions.id"],
            name=op.f("fk_print_archives_revision_id_revisions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["archive_blob"],
            ["blobs.hash"],
            name=op.f("fk_print_archives_archive_blob_blobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["thumbnail_blob"],
            ["blobs.hash"],
            name=op.f("fk_print_archives_thumbnail_blob_blobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_blob"],
            ["blobs.hash"],
            name=op.f("fk_print_archives_snapshot_blob_blobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["timelapse_blob"],
            ["blobs.hash"],
            name=op.f("fk_print_archives_timelapse_blob_blobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_print_archives_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_print_archives_model_id", "print_archives", ["model_id"])
    op.create_index("ix_print_archives_result", "print_archives", ["result"])
    op.create_index(
        "ix_print_archives_created_by_user_id", "print_archives", ["created_by_user_id"]
    )


def downgrade() -> None:
    op.drop_table("print_archives")

    op.execute("DELETE FROM prints WHERE model_id IS NULL")
    for col in (
        "total_cost",
        "energy_cost",
        "energy_kwh",
        "machine_cost",
        "filament_cost",
        "failure_reason",
        "quality",
        "source",
    ):
        op.drop_column("prints", col)
    op.drop_constraint("uq_prints_print_job_id", "prints", type_="unique")
    op.drop_constraint("fk_prints_print_job_id_print_jobs", "prints", type_="foreignkey")
    op.drop_column("prints", "print_job_id")
    op.alter_column("prints", "model_id", existing_type=sa.BigInteger(), nullable=False)

    op.execute("DELETE FROM print_jobs WHERE file_id IS NULL")
    op.alter_column("print_jobs", "file_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column("print_jobs", "plate")
    op.drop_column("print_jobs", "origin")
