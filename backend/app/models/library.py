"""Core library domain: models, tags, notes, revisions, blobs, files.

Principle (SPEC "Data model"): ``blobs`` are content identity (blake3 hash
PK); ``files`` are a path within a revision snapshot pointing at a blob.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, str_enum
from app.models.enums import (
    BlobFormat,
    BlobKind,
    PrintQuality,
    PrintResult,
    PrintSource,
    QueueState,
    ReviewState,
)

if TYPE_CHECKING:
    # Only for static analysis / the `Mapped[...]` string annotations below
    # -- `Blob.meta`/`Blob.derivatives` resolve these by class name via
    # SQLAlchemy's registry at mapper-configure time (both modules are
    # imported together via `app.models`), not via this import.
    from app.models.processing import BlobMeta, Derivative

# Pure many-to-many join table (SPEC: ``model_tags(model_id, tag_id)``) --
# no extra columns, so a plain Core Table (used via ``relationship(secondary=...)``)
# is a better fit than a mapped class.
model_tags = Table(
    "model_tags",
    Base.metadata,
    Column("model_id", BigInteger, ForeignKey("models.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", BigInteger, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)
# Only the composite PK (model_id, tag_id) exists otherwise -- the gallery's
# `tag=` filter looks up by `tag_id` alone (Task 7 brief D1).
Index("ix_model_tags_tag_id", model_tags.c.tag_id)


class Model(Base):
    """A library model (SPEC ``models``)."""

    __tablename__ = "models"
    __table_args__ = (
        # pg_trgm GIN indexes backing gallery search (SPEC: "pg_trgm GIN on
        # models.name/description").
        Index(
            "ix_models_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_models_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
        # Gallery keyset-pagination indexes (Task 7 brief D1): back the
        # `sort=name` and default `-updated_at` keyset predicates
        # (`WHERE (sort_col, id) > (cursor_val, cursor_id) ORDER BY sort_col,
        # id`) with a composite index instead of a full sort. R13b adds
        # `created_at`/`print_count` twins for the "Recently added"/"Most
        # printed" sorts.
        Index("ix_models_name_id", "name", "id"),
        Index("ix_models_updated_at_id", "updated_at", "id"),
        Index("ix_models_created_at_id", "created_at", "id"),
        Index("ix_models_print_count_id", "print_count", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_site: Mapped[str | None] = mapped_column(String)
    source_author: Mapped[str | None] = mapped_column(String)
    source_license: Mapped[str | None] = mapped_column(String)
    # The followed collection this model was imported from, if any (Branch 3
    # Task 1). `source_collection_title` is a DENORMALIZED snapshot taken at
    # import time so provenance survives unfollowing/deleting the collection
    # -- ON DELETE SET NULL then nulls the FK but leaves the title behind.
    # Indexed: the gallery's `collection=` filter is a plain equality on this
    # column (mirrors `ix_blobs_format` for the `format=` filter).
    source_collection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("followed_collections.id", ondelete="SET NULL"), index=True
    )
    source_collection_title: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime | None]
    # Circular FK with revisions.model_id -> models.id: use_alter=True lets
    # SQLAlchemy (and Alembic autogenerate) create both tables first and add
    # this constraint afterwards, avoiding a table-creation-order deadlock.
    current_revision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("revisions.id", use_alter=True, name="fk_models_current_revision_id_revisions"),
    )
    cover_blob_hash: Mapped[str | None] = mapped_column(
        CHAR(64), ForeignKey("blobs.hash", ondelete="SET NULL")
    )
    # NULL = normal; "adopted" = the scanner attached this model out-of-band
    # and it hasn't been reviewed yet (SPEC M3 "Rescan/reconcile"; Task 5
    # brief). `patch_model` allows clearing it so the UI can dismiss the flag.
    review_state: Mapped[str | None] = mapped_column(String)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # A user-starred model (Branch 4 Task 1: favorites). Indexed -- like
    # `source_collection_id`/`format`, the gallery's `favorite=` filter is a
    # plain equality on this column.
    favorite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), index=True
    )
    # Single-valued, exclusive grouping (R13b) -- distinct from `tags`
    # (many-to-many): a model has AT MOST ONE category. ON DELETE SET NULL so
    # deleting a category un-categorizes its models instead of blocking or
    # cascading.
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    # Denormalized count of `prints` rows for this model (R13b Risk
    # resolution 4): the single writer is `app.services.prints`
    # create_print/delete_print (+1/-1); `recount_print_counts` self-heals
    # any drift from the scan job. Backs the "Most printed" gallery sort via
    # the SAME keyset-cursor path as `updated_at`/`name`, which a live
    # `COUNT(*)` subquery couldn't.
    print_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # R13c: free-form user metadata (API field `metadata`; the column is
    # named `metadata_json` because `Base`/SQLAlchemy's declarative machinery
    # reserves the bare attribute name `metadata` on every mapped class).
    # Bounds (key/value length, entry count) are enforced at the schema
    # layer (`app.schemas.library.ModelPatch`), not here.
    metadata_json: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    print_tips: Mapped[str | None] = mapped_column(Text)
    # Trash / recycle bin: NULL = live; set = soft-deleted (hidden from every
    # library query, purged after the retention window or on demand).
    deleted_at: Mapped[datetime | None] = mapped_column(index=True)
    deleted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Ambiguous without foreign_keys=: models<->revisions has two FK paths
    # (revisions.model_id -> models.id, and models.current_revision_id ->
    # revisions.id above).
    revisions: Mapped[list["Revision"]] = relationship(foreign_keys="Revision.model_id")
    tags: Mapped[list["Tag"]] = relationship(secondary=model_tags)
    notes: Mapped[list["Note"]] = relationship()
    category: Mapped["Category | None"] = relationship()


class Category(Base):
    """A user-defined, single-valued category for grouping models (R13b) --
    distinct from ``tags`` (many-to-many join table): a model has at most one
    category, stored as a plain FK column (``Model.category_id``).
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Palette key, same convention as `Tag.color` (see
    # app.schemas.library.TagColor) -- enforced by a Literal at the API layer
    # and a CHECK constraint in the DB.
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Material(Base):
    """A user-defined filament/resin material (R13c): distinct from
    ``prints.filament`` (a free-text per-print snapshot that survives a
    material's deletion via ``ON DELETE SET NULL`` on ``Print.material_id``).
    """

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kind: Mapped[str | None] = mapped_column(String)
    # Free-form hex color (``#RRGGBB``), NOT the fixed `Tag`/`Category`
    # palette -- validated at the schema layer, not by a DB CHECK.
    color: Mapped[str | None] = mapped_column(String)
    vendor: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Tag(Base):
    """A user-defined tag (SPEC ``tags``)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Palette key (see app.schemas.library.TAG_COLORS), not a free-form
    # value -- enforced by a Literal at the API layer and a CHECK
    # constraint in the DB (some paths write model_tags/tags directly).
    color: Mapped[str | None] = mapped_column(String, nullable=True)


class Note(Base):
    """A markdown note on a model or a specific revision (SPEC ``notes``)."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="CASCADE"), nullable=False
    )
    # NULL = model-level note.
    revision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("revisions.id", ondelete="CASCADE")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PrintQueueEntry(Base):
    """One model queued to print, in manual print order (Branch 4 Task 1),
    extended by the P6 slicer/dispatch plan with a slice-and-send pipeline
    (``file_id`` -> ``slicing`` -> ``ready`` -> ``dispatched``).

    ``model_id`` was plain-UNIQUE before P6 (a model can only be queued
    once); P6 relaxes that to a partial unique index over bare entries only
    (``file_id IS NULL``) so a model CAN be queued more than once as long as
    each extra entry names a specific ``file_id`` -- re-adding an
    already-queued BARE model stays idempotent
    (``app.services.queue.enqueue_model`` returns the existing entry rather
    than erroring); a non-bare enqueue is never idempotent (P6 task 6).
    ``position`` is a dense 1..n ranking over the whole queue, renumbered by
    the service layer on every insert/delete/reorder so the UI can always
    render (and PATCH back) a contiguous list.
    """

    __tablename__ = "print_queue"
    __table_args__ = (
        Index(
            "uq_print_queue_model_id_bare",
            "model_id",
            unique=True,
            postgresql_where=text("file_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    # P6 slicer/dispatch plan columns below. `printer_id` is the user's
    # requested/preferred target (NULL = any compatible printer);
    # `assigned_printer_id` is the one the dispatcher actually picked for
    # THIS dispatch attempt (set when `state` becomes `dispatched`).
    printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    recipe_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_recipes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # NULL = a legacy "bare" entry (model only, no specific file chosen yet).
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sliced_file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plate: Mapped[int | None] = mapped_column(Integer)
    # Slice-option overrides (``auto_orient`` / ``arrange``) layered over the
    # recipe's own by this entry's slice -- set by a pipeline's orient/arrange
    # pre-steps (``app.services.pipelines``); NULL = the recipe's options as-is.
    slice_options: Mapped[dict | None] = mapped_column(JSONB)
    copies: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    state: Mapped[QueueState] = mapped_column(
        str_enum(QueueState, "queue_state"),
        nullable=False,
        server_default=text("'waiting'"),
        index=True,
    )
    # Set when `state` becomes `slicing` (the dispatcher's own tick, or
    # `slice_file`'s claim step); the dispatcher's `_reconcile` uses it to
    # detect a `slicing` entry stuck well past a plausible slice duration
    # (worker crash/kill mid-slice) and bounce it back to `waiting`.
    slicing_started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    assigned_printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    print_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Tail of the slicer CLI's output from the last failed slice (the
    # sidecar's `log_tail`); cleared on a fresh attempt.
    last_log: Mapped[str | None] = mapped_column(Text)
    # Celery task id of the in-flight `slice_file`, so cancel can revoke it.
    slice_task_id: Mapped[str | None] = mapped_column(String)
    ams_mapping: Mapped[dict | None] = mapped_column(JSONB)
    print_options: Mapped[dict | None] = mapped_column(JSONB)
    # P7 review/billing plan columns below. `cost_center_id` is the budget
    # this entry reserves/charges against (NULL when billing is off or the
    # creator has none). `review_state` gates dispatch when a review
    # workflow applies (see `app.services.queue.eligible_queue_items`);
    # `reviewed_by`/`reviewed_at`/`review_note` record the approve/reject
    # decision. `reserved_cost` is the ledger `reservation` amount posted
    # at enqueue/slice time; `hold_reason` (e.g. `"over_budget"`) blocks
    # dispatch independent of `state`/`review_state`.
    cost_center_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cost_centers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_state: Mapped[ReviewState] = mapped_column(
        str_enum(ReviewState, "review_state"),
        nullable=False,
        server_default=text("'none'"),
        index=True,
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reserved_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # T4 scheduling columns. ``scheduled_at`` defers dispatch until then;
    # ``manual_start`` holds it until ``released_at`` is set; the previous-success
    # / auto-off / preheat flags are read by the dispatcher; ``stage`` is the
    # preheat sub-status (preheating|soaking).
    scheduled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    manual_start: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    require_previous_success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    auto_off_after: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    preheat_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    preheat_chamber_target: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)


class PrintBatch(Base):
    """T4: a named run of ``quantity`` queue entries (``copies=1`` each).
    ``status`` is draft|active|done|cancelled; ``done`` is derived from the
    entries (``app.services.batches``)."""

    __tablename__ = "print_batches"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="SET NULL"), nullable=True
    )
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    recipe_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_recipes.id", ondelete="SET NULL"), nullable=True
    )
    printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    plate_targets: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    # Scheduling fields applied to every entry at dispatch.
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    cost_center_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cost_centers.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Print(Base):
    """One logged print attempt against a model (Branch 5 Task 1): a
    user-entered per-model print history, distinct from the ``print_queue``
    "to print" worklist above and ``print_jobs``' live send-to-printer
    telemetry (``app.models.printing``, M4).

    ``printer_name`` is a plain TEXT snapshot, NOT an FK to ``printers`` --
    printers are deletable and this history must survive their removal.
    """

    __tablename__ = "prints"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # NULL for an auto-archived run that could not be matched to a model.
    model_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="CASCADE"), nullable=True, index=True
    )
    print_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_jobs.id", ondelete="SET NULL"), unique=True
    )
    source: Mapped[PrintSource] = mapped_column(
        str_enum(PrintSource, "print_source"),
        nullable=False,
        server_default=text("'manual'"),
    )
    quality: Mapped[PrintQuality | None] = mapped_column(str_enum(PrintQuality, "print_quality"))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    filament_cost: Mapped[float | None] = mapped_column(Float)
    machine_cost: Mapped[float | None] = mapped_column(Float)
    energy_kwh: Mapped[float | None] = mapped_column(Float)
    energy_cost: Mapped[float | None] = mapped_column(Float)
    total_cost: Mapped[float | None] = mapped_column(Float)
    printed_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    printer_name: Mapped[str | None] = mapped_column(Text)
    filament: Mapped[str | None] = mapped_column(Text)
    # Grams used, distinct from the free-text `filament` snapshot above
    # (R11-B item 14: print cost estimate + the dashboard's
    # `prints.filament_g_total` stat need an actual number to sum).
    filament_g: Mapped[float | None] = mapped_column(Float)
    # R13c: optional structured material, alongside the `filament` free-text
    # snapshot above (kept for prints logged before a material existed, or
    # never resolved to one). ON DELETE SET NULL -- deleting a material must
    # not delete print history.
    material_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("materials.id", ondelete="SET NULL"), index=True
    )
    result: Mapped[PrintResult] = mapped_column(
        str_enum(PrintResult, "print_result"),
        nullable=False,
        server_default=text("'success'"),
    )
    duration_min: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    material: Mapped["Material | None"] = relationship()


class Revision(Base):
    """A full-snapshot revision of a model (SPEC ``revisions``)."""

    __tablename__ = "revisions"
    __table_args__ = (UniqueConstraint("model_id", "number"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("models.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    dir_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    files: Mapped[list["File"]] = relationship()


class Blob(Base):
    """Content-addressed blob: the unit of dedup and metadata attachment
    (SPEC ``blobs``).
    """

    __tablename__ = "blobs"
    __table_args__ = (
        # Backs the gallery's `format=` filter (Task 7 brief D1).
        Index("ix_blobs_format", "format"),
    )

    hash: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[BlobKind] = mapped_column(str_enum(BlobKind, "blob_kind"), nullable=False)
    format: Mapped[BlobFormat] = mapped_column(str_enum(BlobFormat, "blob_format"), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    # No column changes -- these are relationships only (Task 7 brief), read
    # via `selectinload` by the detail/file-enrichment queries in
    # `app.services.library` to compute `FileOut`'s meta/thumb_ready/
    # glb_status/glb_preview_ready fields without a query per file.
    meta: Mapped["BlobMeta | None"] = relationship()
    derivatives: Mapped[list["Derivative"]] = relationship()


class File(Base):
    """A path within a revision snapshot, pointing at a blob (SPEC ``files``)."""

    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("revision_id", "rel_path"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blob_hash: Mapped[str] = mapped_column(
        CHAR(64), ForeignKey("blobs.hash"), nullable=False, index=True
    )
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Denormalized full storage-backend path, for rescan without re-deriving
    # layout from rel_path.
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    mtime: Mapped[datetime | None]
    verified_at: Mapped[datetime | None]
    # PRIMARY backend to read this file's bytes from (SPEC "Workstream C
    # multi-backend storage"); NULL only transiently on a pre-migration row
    # mid-migration -- the data-seed backfills every existing file to the
    # seeded default backend, and every write path (Workstream C task C2)
    # sets it going forward. `file_locations` (app.models.storage) holds ALL
    # locations (incl. this primary one) for replication bookkeeping.
    backend_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("storage_backends.id"), index=True
    )

    blob: Mapped["Blob"] = relationship()
