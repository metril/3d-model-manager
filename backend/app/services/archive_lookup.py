"""Resolve a print job to its archive, including jobs folded in as an earlier
run of a later reprint (``print_archives.print_job_id`` only tracks the latest)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Print, PrintArchive


def archive_for_job(session: Session, job_id: int) -> PrintArchive | None:
    arc = session.scalars(select(PrintArchive).where(PrintArchive.print_job_id == job_id)).first()
    if arc is not None:
        return arc
    return session.scalars(
        select(PrintArchive)
        .join(Print, Print.archive_id == PrintArchive.id)
        .where(Print.print_job_id == job_id)
    ).first()
