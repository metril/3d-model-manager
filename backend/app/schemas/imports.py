from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, Field, StringConstraints

if TYPE_CHECKING:
    from app.importers.base import RemoteList, SearchResult
    from app.models.system import Import

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ImportCreate(BaseModel):
    url: NonEmptyStr
    # Browser-extension save options: a followed (local) collection to file the
    # model under and tag names to apply once the import completes.
    collection_id: int | None = None
    tags: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    ] = Field(default_factory=list, max_length=20)


class ImportOut(BaseModel):
    id: int
    url: str
    site: str
    external_id: str | None
    state: str
    model_id: int | None
    error: str | None
    meta: dict | None
    dismissed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, imp: Import) -> ImportOut:
        return cls(
            id=imp.id,
            url=imp.url,
            site=imp.site,
            external_id=imp.external_id,
            state=imp.state,
            model_id=imp.model_id,
            error=imp.error,
            meta=imp.meta,
            dismissed_at=imp.dismissed_at,
            created_at=imp.created_at,
            updated_at=imp.updated_at,
        )


class SearchResultOut(BaseModel):
    """Mirrors ``app.importers.base.SearchResult`` (Workstream B task B1,
    ``GET /imports/search``)."""

    site: str
    external_id: str
    title: str
    url: str
    author: str | None = None
    thumbnail_url: str | None = None

    @classmethod
    def from_dataclass(cls, r: SearchResult) -> SearchResultOut:
        return cls(
            site=r.site.value,
            external_id=r.external_id,
            title=r.title,
            url=r.url,
            author=r.author,
            thumbnail_url=r.thumbnail_url,
        )


class RemoteListOut(BaseModel):
    """One of the signed-in user's lists on a site (M8 H), mirroring
    ``app.importers.base.RemoteList``."""

    site: str
    list_id: str
    kind: str
    title: str
    count: int | None = None

    @classmethod
    def from_dataclass(cls, r: RemoteList) -> RemoteListOut:
        return cls(site=r.site.value, list_id=r.list_id, kind=r.kind, title=r.title, count=r.count)


class SiteSearchStatus(BaseModel):
    """Per-site outcome for a federated search (``GET /imports/search`` with no
    ``site``), so the UI can show which sites answered, which errored, and
    which likely have another page. ``status`` is ``ok`` | ``error``."""

    site: str
    count: int
    has_more: bool
    status: str = "ok"
    detail: str | None = None


class SearchResponse(BaseModel):
    """Federated search payload: merged results across the queried sites plus a
    per-site status row. A single-``site`` query returns one ``per_site`` entry."""

    results: list[SearchResultOut]
    per_site: list[SiteSearchStatus]


class ImportTokensIn(BaseModel):
    thingiverse_token: str = ""
    makerworld_token: str = ""


class ImportTokensOut(BaseModel):
    thingiverse_token: str  # "***" when a token is stored, "" otherwise -- never the real value
    makerworld_token: str  # "***" when a token is stored, "" otherwise -- never the real value
