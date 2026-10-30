"""`GET /storage/tree` (R13b): a real drillable file browser over the
canonical storage layout (`<slug>/<rev-dir>/<rel_path>`, see
`app.services.layout.file_key`), derived from `File.storage_path` prefixes
-- no filesystem walk.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import BlobFormat, BlobKind
from app.schemas.library import ModelSummary


class StorageTreeDirOut(BaseModel):
    name: str
    path: str
    file_count: int
    model_count: int


class StorageTreeFileOut(BaseModel):
    id: int
    name: str
    rel_path: str
    size: int
    kind: BlobKind
    format: BlobFormat
    model_slug: str
    blob_hash: str
    revision_id: int


class StorageTreeOut(BaseModel):
    path: str
    dirs: list[StorageTreeDirOut]
    files: list[StorageTreeFileOut]
    total_files: int = 0
    model: ModelSummary | None = None
