"""Multi-backend storage domain (Workstream C "multi-backend storage": run
several storage backends at once; each file's bytes are pinned to one
PRIMARY backend for reads (``files.backend_id``) and may additionally be
replicated onto other backends (``file_locations``)).

``storage_backends.config`` stores the exact same per-backend
``StorageConfig`` JSON shape the legacy single-backend ``settings`` row used
(see ``app.storage.config``), Fernet-encrypted at rest via the SAME
``app.services.storage_config.encrypt_config_secret``/``decrypt_config_row``
seam -- ``scheme`` denormalizes ``config["backend"]`` into its own column so
rows can be listed/joined without decrypting each one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StorageBackendRow(Base):
    """A configured storage backend (SPEC "Workstream C multi-backend
    storage design spec").
    """

    __tablename__ = "storage_backends"
    __table_args__ = (
        # Exactly one row may be the default (write target) backend. A
        # partial unique index -- rather than a plain UniqueConstraint on
        # `is_default`, which would allow at most ONE row total -- lets any
        # number of `is_default=false` rows coexist alongside the single
        # `true` one.
        Index(
            "uq_storage_backends_is_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scheme: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class PendingStorageDelete(Base):
    """A storage object whose delete failed after its DB row was removed.

    Retried by the file reaper; the scanner skips these keys so the leftover
    bytes are not re-adopted as a new draft model. ``backend_id`` NULL means
    the default backend.
    """

    __tablename__ = "pending_storage_deletes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    backend_id: Mapped[int | None] = mapped_column(BigInteger)
    key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class FileLocation(Base):
    """One physical copy of a file's bytes on a backend (replication).

    ``files.backend_id`` picks the PRIMARY (read) location; a file may also
    have a row here for any OTHER backend its bytes have been replicated
    onto (relocate ``mode="replicate"``, Workstream C task C2). The storage
    key is the same on every backend: the owning file's ``storage_path``.
    """

    __tablename__ = "file_locations"

    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    # Deliberately NO ondelete cascade: a backend with any file_locations
    # referencing it must be blocked from deletion
    # (app.services.storage_backends.delete_backend's guardrail) rather than
    # silently orphaning replicated bytes.
    backend_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("storage_backends.id"), primary_key=True, index=True
    )
    verified_at: Mapped[datetime | None]
