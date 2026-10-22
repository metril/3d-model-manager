"""Browser-extension endpoints (M10 Workstream A). Token-gated (see
``app.api.deps.require_api_token``), NOT session-gated -- mounted directly
on ``api_router`` (``app/api/__init__.py``), outside ``protected_router``,
so it carries only its own ``require_api_token`` dependency and none of the
session-cookie surface.

Deliberately narrow: exactly six endpoints, matching what the extension
needs to do (prove it has a live token, start an import, check an import's
status, hand over a MakerWorld cookie, push the real MakerWorld collection
list it can see in the user's authenticated browser, push one of those
collections' items) and nothing else -- a leaked extension token must not be
able to delete models, read other secrets, or touch storage config.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_api_token, require_token_perm
from app.config import Settings, get_settings
from app.db import get_db
from app.importers.registry import get_importer
from app.models import ApiToken, User
from app.models.enums import ImportSite
from app.models.system import Import
from app.schemas.imports import ImportCreate, ImportOut, NonEmptyStr
from app.services import import_tokens, remote_collections
from app.services.imports import start_import
from app.services.permissions import load_grants

_LIB_WRITE = require_token_perm("library:write")

router = APIRouter(prefix="/ext", tags=["ext"], dependencies=[Depends(require_api_token)])

# Abuse guard: a runaway/misbehaving extension push shouldn't be able to
# write an unbounded number of rows in one request.
_MAX_PUSHED_COLLECTIONS = 200
# A single collection's item count is bounded the same way (task 3) -- the
# brief's own cap, chosen well above any real MakerWorld collection size.
_MAX_PUSHED_ITEMS = 500

# Per-field length caps on the strings the extension pushes -- same
# `NonEmptyStr` posture (min_length=1) plus an upper bound, since these are
# scraped straight off a live page and pushed by an unaudited client: an
# unbounded title/url could otherwise write an arbitrarily large row per
# entry, times up to `_MAX_PUSHED_COLLECTIONS`/`_MAX_PUSHED_ITEMS` of them.
_TitleStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
_UrlStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
_IdStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
# `slug` is optional (unlike the fields above) -- `None` is still allowed,
# but a PRESENT slug is held to the same non-empty/max-512 shape as `title`.
_SlugStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class ExtCredentialIn(BaseModel):
    token: NonEmptyStr


class OkOut(BaseModel):
    ok: bool = True


class ImportStatusOut(BaseModel):
    """The token plane's read of an import -- deliberately just these three
    fields, a minimal ``{id, state, error}`` payload: unlike the session-
    gated ``ImportOut``, this omits ``url``/``site``/``external_id``/
    ``model_id``/``meta`` on purpose, so a leaked extension token can't be
    turned into a data-exfiltration read surface over the library. Import
    ids ARE sequential and therefore walkable -- a valid token holder can
    enumerate other imports' state/error by id; that's an accepted
    consequence of a single shared token gating this whole router
    (``require_api_token``), not something this endpoint's narrow shape
    defends against.
    """

    id: int
    state: str
    error: str | None


class ExtCollectionEntryIn(BaseModel):
    list_id: _IdStr
    title: _TitleStr
    slug: _SlugStr | None = None
    count: int | None = None
    is_default: bool = False


class ExtCollectionsPushIn(BaseModel):
    site: ImportSite
    collections: Annotated[list[ExtCollectionEntryIn], Field(max_length=_MAX_PUSHED_COLLECTIONS)]


class ExtCollectionsPushOut(BaseModel):
    ok: bool = True
    count: int


class ExtCollectionItemIn(BaseModel):
    external_id: _IdStr
    title: _TitleStr
    url: _UrlStr
    author: str | None = None
    thumbnail_url: str | None = None


class ExtCollectionItemsPushIn(BaseModel):
    site: ImportSite
    items: Annotated[list[ExtCollectionItemIn], Field(max_length=_MAX_PUSHED_ITEMS)]


class ExtCollectionItemsPushOut(BaseModel):
    ok: bool = True
    count: int


@router.get("/ping", response_model=OkOut)
async def ping() -> OkOut:
    """Validates the token (the dependency already ran); used by the
    extension's "Test connection" action."""
    return OkOut()


@router.post("/imports", status_code=status.HTTP_201_CREATED, response_model=ImportOut)
async def create_import(
    payload: ImportCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(_LIB_WRITE),
) -> ImportOut:
    """Mirrors ``POST /imports`` (``app.api.imports.create_import``) exactly
    -- same ``start_import`` call, same 200-vs-201 dedup signal -- reused
    verbatim rather than reimplemented so the two entry points can't drift.
    """
    imp, created = await start_import(db, payload.url, created_by_user_id=ctx.user.id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ImportOut.from_model(imp)


@router.get("/imports/{import_id}", response_model=ImportStatusOut)
async def get_import_status(import_id: int, db: AsyncSession = Depends(get_db)) -> ImportStatusOut:
    """Lets the popup poll for the real outcome of a save it just made via
    ``POST /ext/imports`` (import-health branch T4) -- the 201/200 from that
    call only means a row exists, not that the import finished or succeeded.
    404 unknown id.
    """
    imp = await db.get(Import, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"import {import_id} not found")
    return ImportStatusOut(id=imp.id, state=imp.state, error=imp.error)


async def _owner_settings_write(
    token: ApiToken = Depends(require_api_token), db: AsyncSession = Depends(get_db)
) -> None:
    """The MakerWorld credential is global: only a token whose OWNER holds
    ``settings:write`` (uncapped grants) may overwrite it."""
    owner = await db.get(User, token.user_id)
    if owner is not None:
        _, perms, _ = await load_grants(db, owner)
        if "settings:write" in perms:
            return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "settings:write required")


@router.post("/credentials/makerworld", response_model=OkOut)
async def set_makerworld_credential(
    payload: ExtCredentialIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthContext = Depends(_LIB_WRITE),
    __: None = Depends(_owner_settings_write),
) -> OkOut:
    """The extension couriers the operator's browser-side MakerWorld cookie
    here. Merge-set: read the currently stored tokens and keep
    ``thingiverse_token`` as-is (mirrors ``_merge_import_token`` in
    ``app.api.settings``) so setting MakerWorld never clobbers a separately
    configured Thingiverse token. The token is never echoed back.
    """
    current = await import_tokens.get_import_tokens(db, settings)
    await import_tokens.set_import_tokens(
        db, settings, thingiverse_token=current.thingiverse_token, makerworld_token=payload.token
    )
    return OkOut()


@router.post("/collections", response_model=ExtCollectionsPushOut)
async def push_collections(
    payload: ExtCollectionsPushIn,
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(_LIB_WRITE),
) -> ExtCollectionsPushOut:
    """The extension pushes the real collection list for ``site`` here --
    it runs in the user's authenticated browser, where MakerWorld's
    Cloudflare wall around the SSR collections route isn't up (see
    ``app.importers.makerworld``'s module docstring). Authoritative full-list
    replace, not an incremental merge: anything cached for ``site`` but not
    in this push is deleted (``app.services.remote_collections
    .replace_site_cache``) -- a collection the user deleted/unfollowed on the
    remote site should disappear from the cache too.
    """
    entries = [
        remote_collections.CacheEntry(
            list_id=entry.list_id,
            title=entry.title,
            slug=entry.slug,
            count=entry.count,
            is_default=entry.is_default,
        )
        for entry in payload.collections
    ]
    count = await remote_collections.replace_site_cache(db, payload.site, entries)
    return ExtCollectionsPushOut(count=count)


@router.post("/collections/{list_id}/items", response_model=ExtCollectionItemsPushOut)
async def push_collection_items(
    list_id: str,
    payload: ExtCollectionItemsPushIn,
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(_LIB_WRITE),
) -> ExtCollectionItemsPushOut:
    """The extension pushes one collection's ITEMS here -- fetched from the
    page origin in the user's own authenticated browser (same Cloudflare-
    evasion posture as ``POST /ext/collections`` above; see
    ``app.models.collections.RemoteCollectionItem``'s docstring for exactly
    why the server-side items endpoint can't be trusted for a named
    collection). Every ``url`` must canonicalize through ``site``'s importer
    -- rejected 422, naming the first bad url, otherwise -- because
    ``MakerWorldImporter.list_list_items``'s cache fallback hands these urls
    straight back out as ``SearchResult.url``, which the UI feeds to ``POST
    /imports`` verbatim; an arbitrary/malformed url pushed here would
    otherwise poison that pipeline. Authoritative full-list replace for this
    ``(site, list_id)``, same posture as the collection-list push.
    """
    importer = get_importer(payload.site)
    if importer is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{payload.site.value} isn't available"
        )
    for item in payload.items:
        if importer.canonicalize(item.url) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"not a valid {payload.site.value} model url: {item.url}",
            )
    entries = [
        remote_collections.ItemEntry(
            external_id=item.external_id,
            title=item.title,
            url=item.url,
            author=item.author,
            thumbnail_url=item.thumbnail_url,
        )
        for item in payload.items
    ]
    count = await remote_collections.replace_list_items(db, payload.site, list_id, entries)
    return ExtCollectionItemsPushOut(count=count)
