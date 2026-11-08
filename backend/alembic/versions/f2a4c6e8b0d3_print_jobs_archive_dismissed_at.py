"""print_jobs.archive_dismissed_at: a deleted run's job must not be re-archived

Revision ID: f2a4c6e8b0d3
Revises: e1f3a5b7c9d2
"""

import sqlalchemy as sa

from alembic import op

revision = "f2a4c6e8b0d3"
down_revision = "e1f3a5b7c9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "print_jobs", sa.Column("archive_dismissed_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Terminal jobs with no archive that are over a day old are deleted/retention-
    # purged runs (a lost enqueue is swept within minutes), so tombstone them to
    # keep the sweep from resurrecting them. Younger ones stay sweepable so a real
    # lost print is still settled.
    op.execute(
        """
        UPDATE print_jobs j SET archive_dismissed_at = now()
        WHERE j.state::text IN ('finished', 'failed', 'canceled')
          AND j.finished_at < now() - interval '1 day'
          AND NOT EXISTS (SELECT 1 FROM print_archives a WHERE a.print_job_id = j.id)
          AND NOT EXISTS (
            SELECT 1 FROM prints p WHERE p.print_job_id = j.id AND p.archive_id IS NOT NULL
          )
        """
    )


def downgrade() -> None:
    op.drop_column("print_jobs", "archive_dismissed_at")
