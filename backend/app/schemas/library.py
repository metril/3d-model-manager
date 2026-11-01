"""Pydantic request/response schemas for the library domain (SPEC "API
surface", Task 5 brief). ``*.from_model`` classmethods map loaded ORM rows
(``app.models.library``) onto these flat response shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.models.enums import BlobFormat, BlobKind
from app.services.costing import price_run

if TYPE_CHECKING:
    from app.models.library import File, Note
    from app.models.processing import BlobMeta
    from app.services.costing import CostRates

# Empty/whitespace-only strings 422 (stripped before the min_length check),
# per Task 5's "empty-name model -> 422" interface decision.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# R13c custom metadata bounds (`ModelPatch.metadata`/`ModelDetail.metadata`):
# a plain, small key/value bag -- not a schema-free document store.
_METADATA_MAX_ENTRIES = 50
_METADATA_MAX_KEY_LEN = 64
_METADATA_MAX_VALUE_LEN = 2000


def _validate_metadata(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > _METADATA_MAX_ENTRIES:
        raise ValueError(f"metadata cannot have more than {_METADATA_MAX_ENTRIES} entries")
    for key, val in value.items():
        if not key or len(key) > _METADATA_MAX_KEY_LEN:
            raise ValueError(f"metadata keys must be 1-{_METADATA_MAX_KEY_LEN} characters")
        if len(val) > _METADATA_MAX_VALUE_LEN:
            raise ValueError(f"metadata values cannot exceed {_METADATA_MAX_VALUE_LEN} characters")
    return value


# -- models -------------------------------------------------------------


class ModelCreate(BaseModel):
    name: NonEmptyStr
    description: str | None = None


class ModelPatch(BaseModel):
    """All fields optional; only the ones present in the request body are
    applied (see ``model_dump(exclude_unset=True)`` in ``app.api.models``).
    Setting ``name`` never changes ``slug``/on-disk directories in M1, but
    does rewrite the ``.3dmm.json`` sidecar (Task 5 brief). ``review_state``
    lets the UI clear the scanner's "adopted, review me" flag. ``is_archived``
    (feat/import-fidelity T3) is the soft-delete toggle -- ``DELETE
    /models/{slug}`` now performs a REAL delete instead (see
    ``app.services.library.hard_delete_model``); archiving/unarchiving a
    model is reversible PATCH traffic, same as any other field here.
    """

    name: NonEmptyStr | None = None
    description: str | None = None
    cover_blob_hash: str | None = None
    review_state: str | None = None
    favorite: bool | None = None
    is_archived: bool | None = None
    # R13b: single-valued category assignment; explicit `null` clears it
    # (unlike `name`, `category_id` is genuinely nullable at the DB level).
    category_id: int | None = None
    # R13c: free-form key/value metadata (maps to `Model.metadata_json`;
    # the API field is named `metadata` -- `metadata` itself is reserved on
    # SQLAlchemy's declarative `Base`, hence the column's different name).
    metadata: dict[str, str] | None = None
    print_tips: str | None = None

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_metadata(value)


class ModelRedownloadIn(BaseModel):
    """``POST /models/{slug}/redownload`` payload (feat/import-fidelity T3):
    dispatches ``app.tasks.importing.redownload_model`` to re-fetch this
    model's files fresh from its original import source. ``mode`` being a
    ``Literal`` means an invalid value 422s here, before a job row is ever
    created -- same posture as ``ModelRelocateIn.mode``.
    """

    mode: Literal["revision", "replace"]


class ModelRelocateIn(BaseModel):
    """``POST /models/{slug}/relocate`` payload (Workstream C task C3):
    dispatches ``app.tasks.relocate.relocate_model_storage`` to move or
    replicate every file of the model, across all its revisions, onto
    ``target_backend_id``. ``mode`` being a ``Literal`` means an invalid
    value 422s here, before a job row is ever created.
    """

    target_backend_id: int
    mode: Literal["move", "replicate"]


class ModelBulkIn(BaseModel):
    """``POST /models/bulk`` payload (Branch 4 Task 1): apply the same
    tag/favorite changes to every model in ``ids`` in one call.
    """

    ids: list[int]
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    favorite: bool | None = None


class ModelBulkOut(BaseModel):
    updated: int


class ModelBulkDeleteIn(BaseModel):
    """``POST /models/bulk-delete`` payload (Round 11 Task 1): hard-delete
    every model in ``ids`` in one call -- see
    ``app.services.library.bulk_hard_delete_models``.
    """

    ids: list[int]


class ModelBulkDeleteOut(BaseModel):
    deleted: int


# -- categories (R13b) ---------------------------------------------------


class ModelCategoryOut(BaseModel):
    """The nested `category` a `ModelSummary`/`ModelDetail` carries -- the
    full CRUD shape (with `model_count`) lives in `app.schemas.categories`.
    """

    id: int
    name: str
    color: str | None = None


class ModelSummary(BaseModel):
    """Gallery list item (Task 5 interface decision; Task 7 adds
    ``print_time_s``/``has_sliced`` and makes ``cover`` a real URL).
    """

    id: int
    slug: str
    name: str
    description: str | None
    tags: list[str]
    tag_refs: list[TagRef] = []
    updated_at: datetime
    created_at: datetime
    file_count: int
    formats: list[BlobFormat]
    cover: str | None = None
    # T2: the revision's own assembly-thumbnail render URL specifically
    # (``None`` until that derivative is OK) -- independent of ``cover``
    # above, which may show a site/user cover image instead even once the
    # render is also ready. Lets the gallery card offer "view the render"
    # separately from whatever it's using as the card's cover.
    render_url: str | None = None
    print_time_s: int | None = None
    has_sliced: bool = False
    source_site: str | None = None
    review_state: str | None = None
    source_collection_id: int | None = None
    source_collection_title: str | None = None
    favorite: bool = False
    # Phase 6: card-level dims + file picks, computed per current revision by
    # `library._gallery_aggregates` (same fixed-query-count batch as the
    # other gallery fields above).
    dims_mm: list[float] | None = None
    best_slicer_file: FileOut | None = None
    printable_file: FileOut | None = None
    # R13b: single-valued category assignment (None = uncategorized).
    category_id: int | None = None
    category: ModelCategoryOut | None = None


class GalleryPage(BaseModel):
    items: list[ModelSummary]
    next_cursor: str | None


# -- blob metadata / plates (Task 7) --------------------------------------

# Mirrors DerivativeStatus's value vocabulary (Task 7 brief): `None` means
# the blob's format never produces a GLB at all, distinct from a GLB-format
# blob that simply hasn't been converted yet ("pending").
GlbStatus = Literal["ok", "pending", "failed", "unsupported"]


class PlateFilamentOut(BaseModel):
    type: str | None
    color: str | None
    used_m: float | None
    used_g: float | None

    @classmethod
    def from_raw(cls, raw: dict) -> PlateFilamentOut:
        return cls(
            type=raw.get("type"),
            color=raw.get("color"),
            used_m=raw.get("used_m"),
            used_g=raw.get("used_g"),
        )


class PlateOut(BaseModel):
    index: int
    prediction_s: int | None
    weight_g: float | None
    thumbnail_available: bool
    est_cost: float | None
    filaments: list[PlateFilamentOut]

    @classmethod
    def from_raw(cls, raw: dict, *, thumbnail_available: bool, rates: CostRates) -> PlateOut:
        return cls(
            index=raw["index"],
            prediction_s=raw.get("prediction_s"),
            weight_g=raw.get("weight_g"),
            thumbnail_available=thumbnail_available,
            est_cost=price_run(raw.get("weight_g"), raw.get("prediction_s"), rates).total_cost,
            filaments=[PlateFilamentOut.from_raw(f) for f in raw.get("filaments") or []],
        )


class BlobMetaOut(BaseModel):
    triangle_count: int | None
    dims_mm: list[float] | None
    volume_cm3: float | None
    surface_area_cm2: float | None
    is_watertight: bool | None
    print_time_s: int | None
    filament_g: float | None
    filament_m: float | None
    filament_types: list[str] | None
    layer_height: float | None
    nozzle: float | None
    printer_model: str | None
    plate_count: int | None
    layer_count: int | None
    infill_pct: float | None
    slicer: str | None
    plates: list[PlateOut] | None

    @classmethod
    def from_model(cls, meta: BlobMeta, plates: list[PlateOut] | None) -> BlobMetaOut:
        return cls(
            triangle_count=meta.triangle_count,
            dims_mm=meta.dims_mm,
            volume_cm3=meta.volume_cm3,
            surface_area_cm2=meta.surface_area_cm2,
            is_watertight=meta.is_watertight,
            print_time_s=meta.print_time_s,
            filament_g=meta.filament_g,
            filament_m=meta.filament_m,
            filament_types=meta.filament_types,
            layer_height=meta.layer_height,
            nozzle=meta.nozzle,
            printer_model=meta.printer_model,
            plate_count=meta.plate_count,
            layer_count=meta.layer_count,
            infill_pct=meta.infill_pct,
            slicer=meta.slicer,
            plates=plates,
        )


@dataclass(slots=True)
class FileEnrichment:
    """Per-blob enrichment for ``FileOut`` (Task 7 interface decision):
    ``FileOut.from_model`` itself has no DB/filesystem access, so the service
    layer (``app.services.library``) computes this ahead of time -- batching
    any filesystem existence checks (plate thumbnails) into one
    ``anyio.to_thread.run_sync`` call per response -- and threads it in.
    """

    meta: BlobMetaOut | None
    thumb_ready: bool
    glb_status: GlbStatus | None
    glb_preview_ready: bool


# -- files / notes --------------------------------------------------------


class FileOut(BaseModel):
    id: int
    revision_id: int
    rel_path: str
    storage_path: str
    blob_hash: str
    size: int
    format: BlobFormat
    kind: BlobKind
    mtime: datetime | None
    verified_at: datetime | None
    meta: BlobMetaOut | None = None
    thumb_ready: bool = False
    glb_status: GlbStatus | None = None
    glb_preview_ready: bool = False
    tag_refs: list[TagRef] = []

    @classmethod
    def from_model(cls, file: File, enrichment: FileEnrichment | None = None) -> FileOut:
        return cls(
            id=file.id,
            revision_id=file.revision_id,
            rel_path=file.rel_path,
            storage_path=file.storage_path,
            blob_hash=file.blob_hash,
            size=file.blob.size,
            format=file.blob.format,
            kind=file.blob.kind,
            mtime=file.mtime,
            verified_at=file.verified_at,
            meta=enrichment.meta if enrichment else None,
            thumb_ready=enrichment.thumb_ready if enrichment else False,
            glb_status=enrichment.glb_status if enrichment else None,
            glb_preview_ready=enrichment.glb_preview_ready if enrichment else False,
        )


class NoteCreate(BaseModel):
    model_id: int
    revision_id: int | None = None
    body: NonEmptyStr


class NotePatch(BaseModel):
    body: NonEmptyStr


class NoteOut(BaseModel):
    id: int
    model_id: int
    revision_id: int | None
    body: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, note: Note) -> NoteOut:
        return cls(
            id=note.id,
            model_id=note.model_id,
            revision_id=note.revision_id,
            body=note.body,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


# -- revisions --------------------------------------------------------


class RevisionCreate(BaseModel):
    name: str | None = None
    note: str | None = None


class RevisionSummary(BaseModel):
    id: int
    model_id: int
    number: int
    name: str | None
    note: str | None
    dir_name: str
    created_at: datetime
    file_count: int


class RevisionDetail(BaseModel):
    id: int
    model_id: int
    number: int
    name: str | None
    note: str | None
    dir_name: str
    created_at: datetime
    files: list[FileOut]
    notes: list[NoteOut]


class ModelBackendOut(BaseModel):
    """One storage backend holding at least one of the model's
    current-revision files (Workstream C task C4) -- the DISTINCT set of
    ``files.backend_id`` values across the current revision, resolved to
    ``{id, name}``. Computed by ``app.services.library._model_backends_summary``
    from the already-loaded revision files (no per-file extra query).
    """

    id: int
    name: str


class ModelDetail(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None
    source_url: str | None
    source_site: str | None
    source_author: str | None
    source_license: str | None
    source_collection_id: int | None = None
    source_collection_title: str | None = None
    imported_at: datetime | None
    cover_blob_hash: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    tags: list[str]
    tag_refs: list[TagRef] = []
    current_revision: RevisionDetail | None
    notes: list[NoteOut]
    review_state: str | None = None
    backends: list[ModelBackendOut] = []
    favorite: bool = False
    # Branch 5 Task 1: print history aggregates, populated by
    # `build_model_detail`'s one count/max(printed_at) query. NOT on
    # `ModelSummary` -- the gallery list doesn't need per-model print stats
    # (keeps `list_models` lean, Branch 5 Task 1 brief).
    print_count: int = 0
    last_printed_at: datetime | None = None
    # R13b: single-valued category assignment (None = uncategorized).
    category_id: int | None = None
    category: ModelCategoryOut | None = None
    # R13c: free-form key/value metadata + a plain-text print-tips note.
    metadata: dict[str, str] | None = None
    print_tips: str | None = None
    created_by_user_id: int | None = None
    created_by_username: str | None = None


# -- diff -------------------------------------------------------------


class DiffEntrySide(BaseModel):
    blob_hash: str
    size: int


class DiffEntry(BaseModel):
    rel_path: str
    a: DiffEntrySide | None
    b: DiffEntrySide | None


class DiffResponse(BaseModel):
    added: list[DiffEntry]
    removed: list[DiffEntry]
    changed: list[DiffEntry]
    unchanged: list[DiffEntry]


# -- tags -------------------------------------------------------------

TagColor = Literal[
    "slate", "red", "orange", "amber", "green", "teal", "blue", "indigo", "violet", "pink"
]


TagHexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
TagIcon = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class TagCreate(BaseModel):
    name: NonEmptyStr
    color: TagHexColor | None = None
    icon: TagIcon | None = None
    description: str | None = None


class TagUpdate(BaseModel):
    """Only fields present in the body are applied (null clears color/icon/description)."""

    name: NonEmptyStr | None = None
    color: TagHexColor | None = None
    icon: TagIcon | None = None
    description: str | None = None


class TagRef(BaseModel):
    """Compact tag reference embedded in entity outputs (``tag_refs``)."""

    id: int
    name: str
    color: str | None = None
    icon: str | None = None
    description: str | None = None


class TagCounts(BaseModel):
    models: int = 0
    model_files: int = 0
    projects: int = 0
    attachments: int = 0
    archives: int = 0
    collections: int = 0
    total: int = 0


class TagOut(TagRef):
    # Only populated by ``GET /tags?with_counts=true``.
    counts: TagCounts | None = None


class TagIdsIn(BaseModel):
    tag_ids: list[int] = Field(default_factory=list, max_length=100)


class FileTagIdsIn(TagIdsIn):
    rel_path: NonEmptyStr


class TagMergeIn(BaseModel):
    into_id: int


class TagBulkMergeIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    into_id: int


class TagBulkUpdateIn(BaseModel):
    """Only fields present are applied (``color: null`` clears)."""

    ids: list[int] = Field(min_length=1, max_length=500)
    color: TagHexColor | None = None


class UsageModel(BaseModel):
    id: int
    slug: str
    name: str
    thumbnail_url: str | None = None


class UsageProject(BaseModel):
    id: int
    name: str
    status: str


class UsageCollection(BaseModel):
    id: int
    title: str
    site: str


class UsageArchive(BaseModel):
    id: int
    name: str | None = None
    result: str
    model_id: int | None = None
    has_thumbnail: bool = False


class TagUsageOut(BaseModel):
    counts: TagCounts
    models: list[UsageModel]
    projects: list[UsageProject]
    collections: list[UsageCollection]
    archives: list[UsageArchive]
