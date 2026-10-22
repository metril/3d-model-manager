"""p6 slicer: slicer_profiles, print_recipes, print_queue dispatch columns

P6 slicer, profiles, recipes and dispatch plan Task 1: ``slicer_profiles``
(imported/hand-entered printer, process, and filament profiles, unique per
``(kind, name)``), ``print_recipes`` (a named combination of profiles plus
fanout options), and the new ``print_queue`` columns that carry an entry
through the slice-and-dispatch pipeline (``file_id`` -> ``slicing`` ->
``ready`` -> ``dispatched``). ``print_queue.model_id``'s plain UNIQUE
becomes a partial unique index over bare entries only (``file_id IS
NULL``) -- a model can now be queued more than once as long as every extra
entry names a specific file.

Revision ID: b4d7e2a9c6f1
Revises: e6b1d4a8c2f7
Create Date: 2026-10-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d7e2a9c6f1"
down_revision: str | Sequence[str] | None = "e6b1d4a8c2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "slicer_profiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", _enum("profile_kind", "printer", "process", "filament"), nullable=False),
        sa.Column("printer_model", sa.String(), nullable=True),
        sa.Column("nozzle_mm", sa.Float(), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "source",
            _enum("profile_source", "import", "manual"),
            server_default="import",
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slicer_profiles")),
        sa.UniqueConstraint("kind", "name", name="uq_slicer_profiles_kind_name"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_slicer_profiles_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_slicer_profiles_created_by_user_id", "slicer_profiles", ["created_by_user_id"]
    )

    op.create_table(
        "print_recipes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("printer_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("process_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("filament_profile_ids", postgresql.JSONB(), nullable=False),
        sa.Column("bed_type", sa.String(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("target_printer_id", sa.BigInteger(), nullable=True),
        sa.Column("target_printer_model", sa.String(), nullable=True),
        sa.Column(
            "fanout",
            _enum("fanout", "single", "spread", "first_n"),
            server_default="single",
            nullable=False,
        ),
        sa.Column("n", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_print_recipes")),
        sa.UniqueConstraint("name", name=op.f("uq_print_recipes_name")),
        sa.ForeignKeyConstraint(
            ["printer_profile_id"],
            ["slicer_profiles.id"],
            name=op.f("fk_print_recipes_printer_profile_id_slicer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_profile_id"],
            ["slicer_profiles.id"],
            name=op.f("fk_print_recipes_process_profile_id_slicer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_printer_id"],
            ["printers.id"],
            name=op.f("fk_print_recipes_target_printer_id_printers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_print_recipes_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_print_recipes_printer_profile_id", "print_recipes", ["printer_profile_id"])
    op.create_index("ix_print_recipes_process_profile_id", "print_recipes", ["process_profile_id"])
    op.create_index("ix_print_recipes_target_printer_id", "print_recipes", ["target_printer_id"])
    op.create_index("ix_print_recipes_created_by_user_id", "print_recipes", ["created_by_user_id"])

    op.add_column("print_queue", sa.Column("printer_id", sa.BigInteger(), nullable=True))
    op.add_column("print_queue", sa.Column("recipe_id", sa.BigInteger(), nullable=True))
    op.add_column("print_queue", sa.Column("file_id", sa.BigInteger(), nullable=True))
    op.add_column("print_queue", sa.Column("sliced_file_id", sa.BigInteger(), nullable=True))
    op.add_column("print_queue", sa.Column("plate", sa.Integer(), nullable=True))
    op.add_column(
        "print_queue",
        sa.Column("copies", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "print_queue",
        sa.Column(
            "state",
            _enum(
                "queue_state", "waiting", "slicing", "ready", "dispatched", "held", "failed", "done"
            ),
            server_default="waiting",
            nullable=False,
        ),
    )
    op.add_column("print_queue", sa.Column("assigned_printer_id", sa.BigInteger(), nullable=True))
    op.add_column("print_queue", sa.Column("print_job_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "print_queue",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("print_queue", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("print_queue", sa.Column("ams_mapping", postgresql.JSONB(), nullable=True))
    op.add_column("print_queue", sa.Column("print_options", postgresql.JSONB(), nullable=True))

    op.create_foreign_key(
        op.f("fk_print_queue_printer_id_printers"),
        "print_queue",
        "printers",
        ["printer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_print_queue_recipe_id_print_recipes"),
        "print_queue",
        "print_recipes",
        ["recipe_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_print_queue_file_id_files"),
        "print_queue",
        "files",
        ["file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_print_queue_sliced_file_id_files"),
        "print_queue",
        "files",
        ["sliced_file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_print_queue_assigned_printer_id_printers"),
        "print_queue",
        "printers",
        ["assigned_printer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_print_queue_print_job_id_print_jobs"),
        "print_queue",
        "print_jobs",
        ["print_job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_print_queue_printer_id", "print_queue", ["printer_id"])
    op.create_index("ix_print_queue_recipe_id", "print_queue", ["recipe_id"])
    op.create_index("ix_print_queue_file_id", "print_queue", ["file_id"])
    op.create_index("ix_print_queue_sliced_file_id", "print_queue", ["sliced_file_id"])
    op.create_index("ix_print_queue_state", "print_queue", ["state"])
    op.create_index("ix_print_queue_assigned_printer_id", "print_queue", ["assigned_printer_id"])
    op.create_index("ix_print_queue_print_job_id", "print_queue", ["print_job_id"])

    # model_id UNIQUE -> partial unique index where file_id IS NULL (a model
    # can be queued more than once as long as every extra entry names a
    # specific file).
    op.drop_constraint(op.f("uq_print_queue_model_id"), "print_queue", type_="unique")
    op.create_index(
        "uq_print_queue_model_id_bare",
        "print_queue",
        ["model_id"],
        unique=True,
        postgresql_where=sa.text("file_id IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # The partial unique index allows duplicate model_id across non-bare
    # rows; a plain UNIQUE can't coexist with that, so non-bare rows are
    # removed before restoring it.
    op.execute("DELETE FROM print_queue WHERE file_id IS NOT NULL")
    op.drop_index("uq_print_queue_model_id_bare", table_name="print_queue")
    op.create_unique_constraint(op.f("uq_print_queue_model_id"), "print_queue", ["model_id"])

    op.drop_index("ix_print_queue_print_job_id", table_name="print_queue")
    op.drop_index("ix_print_queue_assigned_printer_id", table_name="print_queue")
    op.drop_index("ix_print_queue_state", table_name="print_queue")
    op.drop_index("ix_print_queue_sliced_file_id", table_name="print_queue")
    op.drop_index("ix_print_queue_file_id", table_name="print_queue")
    op.drop_index("ix_print_queue_recipe_id", table_name="print_queue")
    op.drop_index("ix_print_queue_printer_id", table_name="print_queue")

    op.drop_constraint(
        op.f("fk_print_queue_print_job_id_print_jobs"), "print_queue", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_print_queue_assigned_printer_id_printers"), "print_queue", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_print_queue_sliced_file_id_files"), "print_queue", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_print_queue_file_id_files"), "print_queue", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_print_queue_recipe_id_print_recipes"), "print_queue", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_print_queue_printer_id_printers"), "print_queue", type_="foreignkey"
    )

    op.drop_column("print_queue", "print_options")
    op.drop_column("print_queue", "ams_mapping")
    op.drop_column("print_queue", "last_error")
    op.drop_column("print_queue", "attempts")
    op.drop_column("print_queue", "print_job_id")
    op.drop_column("print_queue", "assigned_printer_id")
    op.drop_column("print_queue", "state")
    op.drop_column("print_queue", "copies")
    op.drop_column("print_queue", "plate")
    op.drop_column("print_queue", "sliced_file_id")
    op.drop_column("print_queue", "file_id")
    op.drop_column("print_queue", "recipe_id")
    op.drop_column("print_queue", "printer_id")

    op.drop_index("ix_print_recipes_created_by_user_id", table_name="print_recipes")
    op.drop_index("ix_print_recipes_target_printer_id", table_name="print_recipes")
    op.drop_index("ix_print_recipes_process_profile_id", table_name="print_recipes")
    op.drop_index("ix_print_recipes_printer_profile_id", table_name="print_recipes")
    op.drop_table("print_recipes")

    op.drop_index("ix_slicer_profiles_created_by_user_id", table_name="slicer_profiles")
    op.drop_table("slicer_profiles")
