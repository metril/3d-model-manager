"""archive runs: prints.archive_id/printer_id/started_at/finished_at, archive run_count

Revision ID: f3a7b9c2d5e1
Revises: e2f6a8b1d4c5
Create Date: 2026-10-30 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a7b9c2d5e1"
down_revision: str | Sequence[str] | None = "e2f6a8b1d4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("prints", sa.Column("archive_id", sa.BigInteger(), nullable=True))
    op.add_column("prints", sa.Column("printer_id", sa.BigInteger(), nullable=True))
    op.add_column("prints", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prints", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_prints_archive_id_print_archives",
        "prints",
        "print_archives",
        ["archive_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_prints_printer_id_printers",
        "prints",
        "printers",
        ["printer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_prints_archive_id", "prints", ["archive_id"])
    op.add_column(
        "print_archives",
        sa.Column("run_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    # Backfill: each existing archive's run is the auto `prints` row of its job.
    op.execute(
        """
        UPDATE prints p
        SET archive_id = a.id, printer_id = j.printer_id,
            started_at = j.started_at, finished_at = j.finished_at
        FROM print_archives a JOIN print_jobs j ON j.id = a.print_job_id
        WHERE p.print_job_id = a.print_job_id
        """
    )
    # Archives with no run row yet (legacy) get one so run_count is truthful.
    op.execute(
        """
        INSERT INTO prints (model_id, print_job_id, source, result, printed_at, printer_name,
                            duration_min, archive_id, printer_id, started_at, finished_at,
                            created_by_user_id)
        SELECT a.model_id, a.print_job_id, 'auto',
               CASE COALESCE(a.result_override, a.result)
                    WHEN 'completed' THEN 'success' WHEN 'failed' THEN 'fail' ELSE 'partial'
               END,
               COALESCE(j.finished_at, a.created_at), pr.name,
               CASE WHEN a.duration_s IS NULL THEN NULL ELSE round(a.duration_s / 60.0)::int END,
               a.id, j.printer_id, j.started_at, j.finished_at, a.created_by_user_id
        FROM print_archives a
        JOIN print_jobs j ON j.id = a.print_job_id
        LEFT JOIN printers pr ON pr.id = j.printer_id
        WHERE NOT EXISTS (SELECT 1 FROM prints p WHERE p.print_job_id = a.print_job_id)
        """
    )


def downgrade() -> None:
    op.drop_column("print_archives", "run_count")
    op.drop_index("ix_prints_archive_id", table_name="prints")
    op.drop_constraint("fk_prints_printer_id_printers", "prints", type_="foreignkey")
    op.drop_constraint("fk_prints_archive_id_print_archives", "prints", type_="foreignkey")
    op.drop_column("prints", "finished_at")
    op.drop_column("prints", "started_at")
    op.drop_column("prints", "printer_id")
    op.drop_column("prints", "archive_id")
