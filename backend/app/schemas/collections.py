"""Schemas for followed remote collections + the review queue (M8 H)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, StringConstraints

from app.models.enums import CollectionSyncMode, ImportSite

if TYPE_CHECKING:
    from app.models.collections import FollowedCollection, PendingImport

from app.schemas.library import TagRef

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FollowedCollectionOut(BaseModel):
    id: int
    site: str
    list_id: str
    kind: str
    title: str
    mode: str
    last_synced_at: datetime | None
    last_error: str | None
    created_at: datetime
    # Up to 4 member-model thumbnail URLs for the card's 2x2 collage (R11-C
    # item 17); empty when nothing has a ready cover yet.
    preview_thumbnails: list[str] = []
    tag_refs: list[TagRef] = []

    @classmethod
    def from_model(
        cls, row: FollowedCollection, preview_thumbnails: list[str] | None = None
    ) -> FollowedCollectionOut:
        return cls(
            id=row.id,
            site=row.site,
            list_id=row.list_id,
            kind=row.kind,
            title=row.title,
            mode=row.mode,
            last_synced_at=row.last_synced_at,
            last_error=row.last_error,
            created_at=row.created_at,
            preview_thumbnails=preview_thumbnails or [],
        )


class FollowCollectionIn(BaseModel):
    site: ImportSite
    list_id: NonEmptyStr
    kind: str = "collection"
    title: NonEmptyStr
    mode: CollectionSyncMode = CollectionSyncMode.REVIEW


class CollectionModeIn(BaseModel):
    mode: CollectionSyncMode


class FollowFromUrlIn(BaseModel):
    """``POST /collections/from-url`` (M10 escape hatch B): follow a
    MakerWorld collection by pasting its URL instead of picking it off a
    list -- the SSR route that would otherwise enumerate it is intermittently
    Cloudflare-walled (``app.importers.makerworld``'s module docstring)."""

    url: NonEmptyStr
    mode: CollectionSyncMode = CollectionSyncMode.REVIEW


class PendingImportOut(BaseModel):
    id: int
    collection_id: int
    site: str
    external_id: str
    title: str
    url: str
    thumbnail_url: str | None
    created_at: datetime
    # R7 T1: the collection this item should be GROUPED/DISPLAYED under, which
    # is often more specific than the stamped `collection_id` above (that's
    # just whichever list's sync happened to discover it first -- see
    # `app.services.collections.resolve_display_collections`'s docstring).
    group_collection_id: int
    group_title: str

    @classmethod
    def from_model(cls, row: PendingImport, group: tuple[int, str]) -> PendingImportOut:
        group_collection_id, group_title = group
        return cls(
            id=row.id,
            collection_id=row.collection_id,
            site=row.site,
            external_id=row.external_id,
            title=row.title,
            url=row.url,
            thumbnail_url=row.thumbnail_url,
            created_at=row.created_at,
            group_collection_id=group_collection_id,
            group_title=group_title,
        )
