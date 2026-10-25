"""Slicer domain (P6 slicer, profiles, recipes and dispatch plan): imported
slicer profiles and the recipes that combine them into a dispatchable print.

``print_queue``'s new dispatch columns live on ``PrintQueueEntry``
(``app.models.library``), not here -- that table predates this plan.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import Fanout, ProfileKind, ProfileSource


class SlicerProfile(Base):
    """An imported or hand-entered slicer profile (P6 ``slicer_profiles``):
    a printer, process, or filament profile, keyed by ``(kind, name)`` so a
    re-import of the same bundle upserts rather than duplicating.
    """

    __tablename__ = "slicer_profiles"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_slicer_profiles_kind_name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[ProfileKind] = mapped_column(str_enum(ProfileKind, "profile_kind"), nullable=False)
    printer_model: Mapped[str | None] = mapped_column(String)
    nozzle_mm: Mapped[float | None] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[ProfileSource] = mapped_column(
        str_enum(ProfileSource, "profile_source"),
        nullable=False,
        server_default=text("'import'"),
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PrintRecipe(Base):
    """A named, reusable combination of profiles plus dispatch options (P6
    ``print_recipes``): what a queued print slices with and how it fans out
    to printers.
    """

    __tablename__ = "print_recipes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # RESTRICT (not SET NULL/CASCADE): a profile in use by a recipe can't be
    # deleted out from under it (API layer surfaces this as a 409).
    printer_profile_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("slicer_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    process_profile_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("slicer_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # One entry per AMS slot, 1-indexed (slot N -> filament_profile_ids[N-1]);
    # `None` means that slot isn't mapped by this recipe. Not an FK array --
    # validated against `kind=filament` profiles at the schema layer.
    filament_profile_ids: Mapped[list[int | None]] = mapped_column(JSONB, nullable=False)
    bed_type: Mapped[str] = mapped_column(String, nullable=False)
    # {auto_orient, arrange, bed_levelling, flow_cali, timelapse} -- slicer
    # and pre-print toggles passed through to `SliceOptions`/`create_job`.
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    target_printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_printer_model: Mapped[str | None] = mapped_column(String)
    fanout: Mapped[Fanout] = mapped_column(
        str_enum(Fanout, "fanout"), nullable=False, server_default=text("'single'")
    )
    n: Mapped[int | None] = mapped_column(Integer)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SliceJob(Base):
    """A slice-only run (``POST /files/{id}/slice``): slices a file with a
    recipe and stores the ``.gcode.3mf`` on the file's revision, with no
    queue entry or printer involved. ``state`` is a plain string like
    ``jobs.state``: queued | running | done | failed | cancelled."""

    __tablename__ = "slice_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipe_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_recipes.id", ondelete="SET NULL"), nullable=True
    )
    plate: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'queued'"))
    celery_id: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)
    log_tail: Mapped[str | None] = mapped_column(Text)
    sliced_file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    meta: Mapped[dict | None] = mapped_column(JSONB)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
