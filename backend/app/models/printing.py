"""Printer domain: registered printers and their print jobs (SPEC ``printers``,
``print_jobs``).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import PrinterKind, PrintJobState


class Printer(Base):
    """A registered printer (SPEC ``printers``). v1 ships only ``bambu_lan``,
    behind the extensible ``PrinterAdapter`` interface (SPEC "Printer
    integration").
    """

    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[PrinterKind] = mapped_column(str_enum(PrinterKind, "printer_kind"), nullable=False)
    # SPEC literally types this ``inet``, but the SMB backend section allows
    # DNS-name addressing too and ``inet`` would reject hostnames -- String
    # is the safe superset (accepts both a static IP and a DNS/mDNS name).
    host: Mapped[str] = mapped_column(String, nullable=False)
    serial: Mapped[str] = mapped_column(String, nullable=False)
    access_code_enc: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    options: Mapped[dict | None] = mapped_column(JSONB)
    # R13c: `{x, y, z}` mm, used by `GcodePreview`'s build-plate render.
    # Seeded from a small model-name -> volume map on create (see
    # `app.schemas.printers.seed_build_volume_mm`); editable afterward.
    build_volume_mm: Mapped[dict | None] = mapped_column(JSONB)


class PrintJob(Base):
    """A send-to-printer job and its live/last-known status (SPEC
    ``print_jobs``).
    """

    __tablename__ = "print_jobs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    printer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("printers.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("files.id"), nullable=False)
    subtask_name: Mapped[str | None] = mapped_column(String)
    state: Mapped[PrintJobState] = mapped_column(
        str_enum(PrintJobState, "print_job_state"), nullable=False, index=True
    )
    progress_pct: Mapped[float | None] = mapped_column(Float)
    remaining_min: Mapped[int | None] = mapped_column(Integer)
    layer: Mapped[int | None] = mapped_column(Integer)
    total_layers: Mapped[int | None] = mapped_column(Integer)
    printer_error: Mapped[str | None] = mapped_column(Text)
    # SPEC shorthand "timestamps" -- print_jobs has a genuine
    # queued/started/finished lifecycle (see the state enum), so all three
    # are kept distinct rather than collapsing to created_at alone.
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    raw_status: Mapped[dict | None] = mapped_column(JSONB)
