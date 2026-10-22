"""System/ops domain: gallery imports, background job tracking, scanner runs,
and app settings (SPEC ``imports``, ``jobs``, ``scan_runs``, ``settings``).
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import ImportSite, ImportState


class Import(Base):
    """A gallery import job (SPEC ``imports``; provenance capture per
    requirement 8).
    """

    __tablename__ = "imports"
    __table_args__ = (
        # M8 H: the cross-import dedup guard (app.services.import_dedup) looks a
        # remote model up by its identity pair on every manual import AND on
        # every periodic collection-sync item. Deliberately NOT unique -- see
        # that module's docstring (pre-guard installs may hold duplicates).
        Index("ix_imports_site_external_id", "site", "external_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    site: Mapped[ImportSite] = mapped_column(str_enum(ImportSite, "import_site"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String)
    state: Mapped[ImportState] = mapped_column(
        str_enum(ImportState, "import_state"), nullable=False
    )
    model_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="SET NULL")
    )
    # The followed collection this import came from (AUTO-mode sync, or an
    # approved review item), if any -- NULL for a manual `POST /imports`/`POST
    # /ext/imports`. Branch 3 Task 1 provenance capture.
    collection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("followed_collections.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Job(Base):
    """Generic background-job tracking row, UI-visible with a retry button
    (SPEC ``jobs``; "Processing pipeline": "every task tracked in jobs").
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    celery_id: Mapped[str | None] = mapped_column(String)
    type: Mapped[str] = mapped_column(String, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String)
    subject_id: Mapped[int | None] = mapped_column(BigInteger)
    # Not annotated ``enum`` in SPEC (unlike e.g. print_jobs.state), so kept
    # as a plain string rather than inventing a value list.
    state: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ScanRun(Base):
    """One run of the library scanner/reconciler (SPEC ``scan_runs``)."""

    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None]
    # Not annotated ``enum`` in SPEC, kept as a plain string (see Job.state).
    state: Mapped[str] = mapped_column(String, nullable=False)
    files_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files_hashed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    relinked: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    adopted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    missing: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    report: Mapped[dict | None] = mapped_column(JSONB)


class Setting(Base):
    """A single app setting (SPEC ``settings``)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
