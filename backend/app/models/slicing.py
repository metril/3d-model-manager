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
    Index,
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
    __table_args__ = (
        UniqueConstraint("kind", "name", name="uq_slicer_profiles_kind_name"),
        Index(
            "uq_slicer_profiles_cloud",
            "cloud_source",
            "cloud_id",
            unique=True,
            postgresql_where=text("cloud_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[ProfileKind] = mapped_column(str_enum(ProfileKind, "profile_kind"), nullable=False)
    printer_model: Mapped[str | None] = mapped_column(String)
    nozzle_mm: Mapped[float | None] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # System-owned scope metadata derived from ``data`` (never user-edited; see
    # ``slicer_profiles.derive_profile_meta``): the single canonical model key
    # when all ``compat`` entries share one, the full ``[{model_key,
    # nozzle_mm|null}]`` list, and the process layer height. ``compat IS NULL
    # AND printer_model_key IS NULL`` = universal ("any printer").
    printer_model_key: Mapped[str | None] = mapped_column(String, index=True)
    compat: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    layer_height_mm: Mapped[float | None] = mapped_column(Float)
    source: Mapped[ProfileSource] = mapped_column(
        str_enum(ProfileSource, "profile_source"),
        nullable=False,
        server_default=text("'import'"),
    )
    # Cloud preset sync (Orca Cloud / Bambu Cloud): where this row was pulled
    # from, its id there, and two hashes taken at the last sync -- of the
    # remote content and of the stored ``data`` -- so a resync can tell
    # "changed upstream" from "edited locally" (see ``cloud_presets``).
    cloud_source: Mapped[str | None] = mapped_column(String)
    cloud_id: Mapped[str | None] = mapped_column(String)
    cloud_hash: Mapped[str | None] = mapped_column(String)
    cloud_updated: Mapped[str | None] = mapped_column(String)  # remote updated_time at last sync
    data_hash: Mapped[str | None] = mapped_column(String)
    cloud_synced_at: Mapped[datetime | None] = mapped_column()
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
    # Set when started by ``POST /slicer-pipelines/{id}/run``: the pipeline
    # whose post-steps run on completion, and the slice-option overrides its
    # pre-steps (orient/arrange) apply on top of the recipe's own.
    pipeline_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("slicer_pipelines.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    options: Mapped[dict | None] = mapped_column(JSONB)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SlicerPipeline(Base):
    """A named, ordered slice workflow: ``steps`` (pre-steps ``orient`` /
    ``arrange``, then the slice with ``recipe_id``, then post-steps ``rename``
    or ``queue``) run in one click on a file. See ``app.services.pipelines``."""

    __tablename__ = "slicer_pipelines"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String)
    # RESTRICT: a recipe used by a pipeline can't be deleted out from under it.
    recipe_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("print_recipes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    plate: Mapped[int | None] = mapped_column(Integer)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
