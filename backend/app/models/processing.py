"""Processing/pipeline domain: per-blob metadata and generated derivatives.

SPEC "Data model": a file unchanged across revisions shares one blob, so
extraction (``blob_meta``) and rendering (``derivatives``/``assembly_thumbs``)
happen at most once per blob (or per revision, for assemblies).
"""

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CHAR,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import DerivativeKind, DerivativeStatus


class BlobMeta(Base):
    """Extracted mesh/slice metadata for a blob (SPEC ``blob_meta``)."""

    __tablename__ = "blob_meta"
    __table_args__ = (
        # Backs the gallery's `has_sliced=` filter (Task 7 brief D1); partial
        # on `IS NOT NULL` since only sliced blobs (a minority) need to be
        # findable this way.
        Index(
            "ix_blob_meta_print_time_s",
            "print_time_s",
            postgresql_where=text("print_time_s IS NOT NULL"),
        ),
    )

    blob_hash: Mapped[str] = mapped_column(
        CHAR(64), ForeignKey("blobs.hash", ondelete="CASCADE"), primary_key=True
    )
    triangle_count: Mapped[int | None] = mapped_column(BigInteger)
    dims_mm: Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    volume_cm3: Mapped[float | None] = mapped_column(Float)
    surface_area_cm2: Mapped[float | None] = mapped_column(Float)
    is_watertight: Mapped[bool | None] = mapped_column(Boolean)
    print_time_s: Mapped[int | None] = mapped_column(Integer)
    filament_g: Mapped[float | None] = mapped_column(Float)
    filament_m: Mapped[float | None] = mapped_column(Float)
    filament_types: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    layer_height: Mapped[float | None] = mapped_column(Float)
    nozzle: Mapped[float | None] = mapped_column(Float)
    printer_model: Mapped[str | None] = mapped_column(Text)
    plate_count: Mapped[int | None] = mapped_column(Integer)
    layer_count: Mapped[int | None] = mapped_column(Integer)
    infill_pct: Mapped[float | None] = mapped_column(Float)
    slicer: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    # Unsliced multi-plate 3MF plates: [{index,name,object_count,has_glb,bbox,fits}].
    # NULL = never analysed; [] = analysed, fewer than two plates.
    plates_json: Mapped[list | None] = mapped_column(JSONB)
    # Analysis-output version of ``plates_json`` (``tasks.pipeline.PLATES_VERSION``); NULL = stale.
    plates_version: Mapped[int | None] = mapped_column(Integer)


class Derivative(Base):
    """A generated per-blob artifact: thumbnail or GLB (SPEC ``derivatives``)."""

    __tablename__ = "derivatives"
    __table_args__ = (UniqueConstraint("blob_hash", "kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    blob_hash: Mapped[str] = mapped_column(
        CHAR(64), ForeignKey("blobs.hash", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[DerivativeKind] = mapped_column(
        str_enum(DerivativeKind, "derivative_kind"), nullable=False
    )
    local_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DerivativeStatus] = mapped_column(
        str_enum(DerivativeStatus, "derivative_status"),
        nullable=False,
        server_default=DerivativeStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    tool: Mapped[str | None] = mapped_column(Text)
    # GLB derivatives: ``convert.GLB_VERSION`` they were written at (0 = legacy
    # Z-up, -1 = regeneration failed, never retried).
    version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0", default=0
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class AssemblyThumb(Base):
    """Rendered thumbnail for a whole revision's assembly (SPEC
    ``assembly_thumbs``). Reuses ``DerivativeStatus`` for ``status`` -- SPEC
    leaves the value list implicit here, having just spelled it out for
    ``derivatives.status`` immediately above in the same schema block.
    """

    __tablename__ = "assembly_thumbs"

    revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("revisions.id", ondelete="CASCADE"), primary_key=True
    )
    local_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DerivativeStatus] = mapped_column(
        str_enum(DerivativeStatus, "assembly_thumb_status"),
        nullable=False,
        server_default=DerivativeStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
