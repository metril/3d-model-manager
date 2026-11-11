"""Printables importer over the UNOFFICIAL GraphQL API (SPEC/FULL line 229:
POST api.printables.com/graphql/, print(id:) + getDownloadLink, browser-like
UA, anonymous for free models; Club/paid rejected). ALL GraphQL is isolated
in this one module (the SPEC's contract-test seam) -- ``_client`` is the ONE
seam tests monkeypatch. A Cloudflare tightening would be handled by a
cloudscraper fallback hook (documented later escalation, not a v1 dep)."""

from __future__ import annotations

import json
import re
import time
from typing import ClassVar
from urllib.parse import urlparse

import httpx

from app.importers.base import (
    ImportFile,
    ImportMetadata,
    RemoteList,
    ResolvedDownload,
    SearchResult,
    safe_filename,
)
from app.importers.registry import register_importer
from app.models.enums import ImportSite

_GRAPHQL_URL = "https://api.printables.com/graphql/"
_IMG_BASE = "https://media.printables.com/"
_ALLOWED_HOSTS = {"printables.com", "www.printables.com"}
# Printables URLs: /model/<numericId>-<slug> (the id is the numeric prefix).
_ID_RE = re.compile(r"model/(\d+)", re.IGNORECASE)
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

PRINT_QUERY = """
query PrintProfile($id: ID!) {
  print(id: $id) {
    id name description
    user { publicUsername }
    license { name }
    tags { name }
    image { filePath }
    images { filePath }
    premium
    stls { id name fileSize }
  }
}
""".strip()
# `images { filePath }` (T2, gallery download) is UNVERIFIED against the live
# schema -- introspection/grounding only confirmed the singular cover `image`
# field (SPEC/FULL line 229); this plural sibling is a best guess at the
# gallery-list shape, named/shaped consistently with `image` and with
# `searchPrints2`'s own `image { filePath }` below. `_image_urls` parses it
# tolerantly (missing key, empty list, or an item without `filePath` all
# degrade to "just the cover", never an error) -- but if the live field name
# or nesting differs, the GraphQL response would carry a top-level `errors`
# entry for it, which `_post` turns into a hard `RuntimeError` that would
# regress `fetch_metadata` itself. MUST be reconciled against a live query
# before this is trusted in production (mirrors makerworld.py's own
# UNVERIFIED download posture).

# Discovered live (Workstream B task B1) via GraphQL introspection on the
# anonymous endpoint: the root Query type has no field literally named
# "search" -- `searchPrints2(query:, offset:, limit:)` is the one that
# actually text-filters (confirmed: query="benchy" returns Benchy-relevant
# hits, a nonsense query returns none). `quickSearchPrints` also exists (the
# omnibox typeahead) but takes no offset/limit, so it can't page.
SEARCH_QUERY = """
query SearchPrints($query: String!, $limit: Int, $offset: Int) {
  searchPrints2(query: $query, limit: $limit, offset: $offset) {
    totalCount
    items { id name image { filePath } user { publicUsername } }
  }
}
""".strip()

_SEARCH_PAGE_SIZE = 20

DOWNLOAD_MUTATION = """
mutation GetDownloadLink(
  $printId: ID!
  $files: [DownloadFileInput]!
  $source: DownloadSourceEnum!
) {
  getDownloadLink(printId: $printId, files: $files, source: $source) {
    ok output { link }
  }
}
""".strip()


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_GRAPHQL_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "Origin": "https://www.printables.com",
            "Referer": "https://www.printables.com/",
        },
    )


def _authed_client(token: str) -> httpx.Client:
    """Bearer-authenticated GraphQL seam (Workstream A task A1; mirrors
    ``thingiverse._client``) -- same as ``_client()`` plus the
    ``Authorization`` header, kept as its own module-level function so tests
    can monkeypatch it independently of the anonymous ``_client``. Used by
    ``fetch_identity`` now, and by the A4 list/likes queries next."""
    return httpx.Client(
        base_url=_GRAPHQL_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "Origin": "https://www.printables.com",
            "Referer": "https://www.printables.com/",
            "Authorization": f"Bearer {token}",
        },
    )


def _printables_session() -> str:
    """A currently-valid Printables access token for worker use (mirrors
    ``makerworld._bambu_session()``'s pattern exactly: local imports of
    ``get_settings``/``sync_session``/``get_access_token_sync`` so this
    module stays importable without a DB/settings context at import time).
    Raises ``app.services.printables_auth.PrintablesAuthError`` when no
    account is connected (or its stored refresh token can no longer produce
    an access token) -- callers decide how to handle that."""
    from app.config import get_settings
    from app.services import printables_auth
    from app.tasks.base import sync_session

    settings = get_settings()
    with sync_session() as s:
        return printables_auth.get_access_token_sync(s, settings)


IDENTITY_QUERY = """
{ me { id publicUsername } }
""".strip()


def fetch_identity(access_token: str) -> tuple[str | None, str | None]:
    """``(user_id, publicUsername)`` for the connected Printables account
    (Workstream A task A1; live-verified: ``POST /graphql/`` with
    ``Authorization: Bearer <access_jwt>``, ``{"query":"{ me { id
    publicUsername } }"}`` -> ``{"data":{"me":{"id":"5092991",
    "publicUsername":"..."}}}``). The JWT's own ``sub`` claim is the *Prusa
    account id*, NOT the Printables ``userId`` -- always resolve it via this
    query rather than decoding the token. ALL GraphQL stays inside this
    module (this file's own docstring's contract-test seam); this is why
    ``app.api.settings`` imports ``fetch_identity`` from here rather than
    querying Printables itself."""
    with _authed_client(access_token) as c:
        me = _post(c, IDENTITY_QUERY, {}).get("me") or {}
    user_id = me.get("id")
    return (str(user_id) if user_id is not None else None, me.get("publicUsername"))


def _post(client: httpx.Client, query: str, variables: dict) -> dict:
    r = client.post("", json={"query": query, "variables": variables})
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"Printables GraphQL error: {body['errors']}")
    return body["data"]


# -- saved collections / likes (M8 H / Workstream A task A4) -----------------
# Live-verified 2026-07-10 against a real connected account. `userId` is NOT
# the JWT `sub` -- always resolve it via `fetch_identity()`.

USER_COLLECTIONS_QUERY = """
query UserCollections($userId: ID!) {
  collections: userCollections(userId: $userId) {
    id name private likesCount modelsCount: printsCount
  }
}
""".strip()

# `ordering` is MANDATORY (live-verified: omitting it returns HTTP 200
# carrying a GraphQL error -- "Cannot resolve keyword 'new_uploads' into
# field" -- and `models: null`). We always pass "added_to_collection".
COLLECTION_MODELS_QUERY = """
query CollectionModels($collectionId: ID!, $limit: Int, $cursor: String,
                       $ordering: CollectionPrintsOrderingEnum) {
  models: moreCollectionModels(collectionId: $collectionId, limit: $limit,
                               cursor: $cursor, ordering: $ordering) {
    cursor items { id model: print { id name slug user { publicUsername } image { filePath } } }
  }
}
""".strip()

# `printType` is non-null in the schema; we always pass "all".
LIKED_MODELS_QUERY = """
query LikedModels($userId: ID!, $limit: Int, $cursor: String,
                  $printType: PrintTypeOptionsEnum!) {
  models: moreLikedPrints2(likedUserId: $userId, limit: $limit, cursor: $cursor,
                           printType: $printType) {
    cursor items { id model: print { id name slug user { publicUsername } image { filePath } } }
  }
}
""".strip()


def _fetch_page(client: httpx.Client, query: str, variables: dict) -> tuple[list[dict], str]:
    """One page of a cursor-paginated list query (``moreCollectionModels`` /
    ``moreLikedPrints2``) -- both alias their payload to ``models`` and both
    return ``{cursor, items}``, so the two list-items queries share this."""
    data = _post(client, query, variables)
    models = data.get("models") or {}
    return list(models.get("items") or []), models.get("cursor") or ""


# Per-list cursor memo so walking pages 1..N costs N requests of one page each
# instead of re-fetching 1..page items every time (quadratic). Keyed by the
# query + variables + the page the cursor FOLLOWS; entries are short-lived
# because cursors are opaque server state.
_CURSOR_TTL_S = 300.0
_CURSOR_MAX = 256
_cursors: dict[tuple[str, str, int], tuple[str, float]] = {}


def _remember_cursor(key: tuple[str, str], page: int, cursor: str) -> None:
    if len(_cursors) >= _CURSOR_MAX:
        _cursors.clear()
    _cursors[(*key, page)] = (cursor, time.monotonic())


def _paged_items(client: httpx.Client, query: str, base_variables: dict, page: int) -> list[dict]:
    """Printables pages by opaque cursor, the importer Protocol by number.
    When the cursor that follows page ``page - 1`` is still memoised (a
    sequential walk), fetch just this page from it. Otherwise ask for
    `page * _SEARCH_PAGE_SIZE` in one request and return the last window of
    it. If the server caps `limit` -- it hands back fewer items than asked
    WITH a non-empty cursor -- keep following the cursor until the window is
    filled or the list ends (`cursor == ""`)."""
    key = (query, json.dumps(base_variables, sort_keys=True, default=str))
    prev = _cursors.get((*key, page - 1)) if page > 1 else None
    if prev is not None and time.monotonic() - prev[1] < _CURSOR_TTL_S:
        items: list[dict] = []
        cursor = prev[0]
        while len(items) < _SEARCH_PAGE_SIZE and cursor:
            more, cursor = _fetch_page(
                client,
                query,
                {**base_variables, "limit": _SEARCH_PAGE_SIZE - len(items), "cursor": cursor},
            )
            if not more:
                break
            items.extend(more)
        if cursor and len(items) >= _SEARCH_PAGE_SIZE:
            _remember_cursor(key, page, cursor)
        return items
    want = page * _SEARCH_PAGE_SIZE
    items, cursor = _fetch_page(client, query, {**base_variables, "limit": want, "cursor": None})
    while len(items) < want and cursor:
        more, cursor = _fetch_page(
            client, query, {**base_variables, "limit": want - len(items), "cursor": cursor}
        )
        if not more:
            break  # server contradicted itself; don't spin
        items.extend(more)
    if cursor and len(items) >= want:
        _remember_cursor(key, page, cursor)
    return items[(page - 1) * _SEARCH_PAGE_SIZE :]


def _image_urls(p: dict) -> list[str]:
    """Cover-first, deduped gallery picture list (T2) for print JSON ``p``:
    the singular ``image.filePath`` (the existing, verified cover) plus every
    ``images[].filePath`` (the UNVERIFIED gallery field -- see PRINT_QUERY's
    comment), all resolved through the same ``_IMG_BASE`` the cover already
    uses. Tolerant of a missing/empty/malformed ``images`` list -- degrades
    to just the cover, never raises."""
    urls: list[str] = []
    seen: set[str] = set()
    image = p.get("image") or {}
    cover_path = image.get("filePath")
    if cover_path:
        cover = f"{_IMG_BASE}{cover_path}"
        urls.append(cover)
        seen.add(cover)
    for img in p.get("images") or []:
        path = (img or {}).get("filePath")
        if not path:
            continue
        url = f"{_IMG_BASE}{path}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


class PrintablesImporter:
    site: ClassVar[ImportSite] = ImportSite.PRINTABLES

    def canonicalize(self, url: str) -> str | None:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None
        if host not in _ALLOWED_HOSTS:
            return None
        m = _ID_RE.search(url)
        return m.group(1) if m else None

    def _print(self, external_id: str) -> dict:
        with _client() as c:
            return _post(c, PRINT_QUERY, {"id": external_id})["print"]

    def fetch_metadata(self, external_id: str) -> ImportMetadata:
        p = self._print(external_id)
        reject = None
        if p.get("premium"):
            reject = (
                "This is a Printables Club / paid model and can't be imported (login required)."
            )
        image = p.get("image") or {}
        cover = f"{_IMG_BASE}{image['filePath']}" if image.get("filePath") else None
        return ImportMetadata(
            site=self.site,
            external_id=str(external_id),
            source_url=f"https://www.printables.com/model/{external_id}",
            title=p.get("name") or f"print {external_id}",
            description=p.get("description"),
            author=(p.get("user") or {}).get("publicUsername"),
            license=(p.get("license") or {}).get("name"),
            cover_url=cover,
            image_urls=_image_urls(p),
            tags=tuple(t["name"] for t in (p.get("tags") or []) if t.get("name")),
            reject_reason=reject,
        )

    def list_files(self, external_id: str) -> list[ImportFile]:
        p = self._print(external_id)
        return [
            ImportFile(
                remote_id=str(s["id"]),
                filename=safe_filename(s.get("name")),
                url=None,
                size=s.get("fileSize"),
            )
            for s in (p.get("stls") or [])
            if s.get("id") and s.get("name")
        ]

    def resolve_download(self, external_id: str, file: ImportFile) -> ResolvedDownload:
        with _client() as c:
            data = _post(
                c,
                DOWNLOAD_MUTATION,
                {
                    "printId": external_id,
                    "source": "model_detail",
                    "files": [{"fileType": "stl", "ids": [file.remote_id]}],
                },
            )
        out = (data.get("getDownloadLink") or {}).get("output") or {}
        link = out.get("link")
        if not link:
            raise RuntimeError(f"Printables returned no download link for file {file.remote_id}")
        return ResolvedDownload(url=link, filename=file.filename)

    def search(self, query: str, page: int = 1) -> list[SearchResult]:
        if not query:
            return []
        offset = max(page - 1, 0) * _SEARCH_PAGE_SIZE
        with _client() as c:
            data = _post(
                c, SEARCH_QUERY, {"query": query, "limit": _SEARCH_PAGE_SIZE, "offset": offset}
            )
        items = (data.get("searchPrints2") or {}).get("items") or []
        results = []
        for item in items:
            pid = item.get("id")
            if not pid:
                continue
            image = item.get("image") or {}
            cover = f"{_IMG_BASE}{image['filePath']}" if image.get("filePath") else None
            results.append(
                SearchResult(
                    site=self.site,
                    external_id=str(pid),
                    title=item.get("name") or f"print {pid}",
                    url=f"https://www.printables.com/model/{pid}",
                    author=(item.get("user") or {}).get("publicUsername"),
                    thumbnail_url=cover,
                )
            )
        return results

    # -- saved collections / likes (M8 H) ---------------------------------
    # The authenticated session (Workstream A task A1, `app.services.
    # printables_auth` + `_printables_session()`/`_authed_client` above)
    # backs both queries below: `userCollections` for the named collections,
    # and (Printables has no real "likes" collection, mirroring
    # `thingiverse.py`'s synthetic list) `moreLikedPrints2` behind one
    # synthetic "likes" pseudo-list. Not connected (`PrintablesAuthError`)
    # -> [] for both -- the "nothing to show, not an error" convention
    # `search` uses.

    def list_user_lists(self) -> list[RemoteList]:
        from app.services.printables_auth import PrintablesAuthError

        try:
            token = _printables_session()
        except PrintablesAuthError:
            return []
        user_id, _ = fetch_identity(token)
        if not user_id:
            return []
        with _authed_client(token) as c:
            data = _post(c, USER_COLLECTIONS_QUERY, {"userId": user_id})
        lists: list[RemoteList] = []
        for coll in data.get("collections") or []:
            cid = coll.get("id")
            if not cid:
                continue
            lists.append(
                RemoteList(
                    site=self.site,
                    list_id=str(cid),
                    kind="collection",
                    title=coll.get("name") or f"collection {cid}",
                    count=coll.get("modelsCount"),
                )
            )
        lists.append(
            RemoteList(
                site=self.site, list_id="likes", kind="likes", title="Liked models", count=None
            )
        )
        return lists

    def list_list_items(self, list_id: str, page: int = 1) -> list[SearchResult]:
        from app.services.printables_auth import PrintablesAuthError

        try:
            token = _printables_session()
        except PrintablesAuthError:
            return []
        if list_id == "likes":
            user_id, _ = fetch_identity(token)
            if not user_id:
                return []
            query = LIKED_MODELS_QUERY
            base_variables: dict = {"userId": user_id, "printType": "all"}
        else:
            query = COLLECTION_MODELS_QUERY
            base_variables = {"collectionId": list_id, "ordering": "added_to_collection"}
        with _authed_client(token) as c:
            items = _paged_items(c, query, base_variables, page)
        results: list[SearchResult] = []
        for item in items:
            m = item.get("model") or {}
            mid = m.get("id")
            if not mid:
                continue
            slug = m.get("slug")
            url = (
                f"https://www.printables.com/model/{mid}-{slug}"
                if slug
                else f"https://www.printables.com/model/{mid}"
            )
            image = m.get("image") or {}
            thumb = f"{_IMG_BASE}{image['filePath']}" if image.get("filePath") else None
            results.append(
                SearchResult(
                    site=self.site,
                    external_id=str(mid),
                    title=m.get("name") or f"print {mid}",
                    url=url,
                    author=(m.get("user") or {}).get("publicUsername"),
                    thumbnail_url=thumb,
                )
            )
        return results


register_importer(PrintablesImporter())
