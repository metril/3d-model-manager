"""Thingiverse importer over the official REST API (SPEC/FULL line 228:
GET /things/{id}, zip_data.files[]/images[], Authorization: Bearer). The
app token is read from Settings (import_tokens) inside the worker. License
strings are mapped manually (FULL: "Manyfold distrusts the field"). ``_client``
is the ONE seam tests monkeypatch with an httpx.MockTransport."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import quote, urlparse

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

_BASE_URL = "https://api.thingiverse.com"
_ALLOWED_HOSTS = {"thingiverse.com", "www.thingiverse.com"}
_ID_RE = re.compile(r"(?:thing:|.*?[?&]thing=)(\d+)", re.IGNORECASE)
_UA = "3d-model-manager/1.0 (+https://github.com/metril/3d-model-manager)"
_SEARCH_PAGE_SIZE = 20

# Manual license map (FULL line 228). Falls back to the raw string.
_LICENSE_MAP = {
    "creative commons - attribution": "CC-BY-4.0",
    "creative commons - attribution - share alike": "CC-BY-SA-4.0",
    "creative commons - attribution - no derivatives": "CC-BY-ND-4.0",
    "creative commons - attribution - non-commercial": "CC-BY-NC-4.0",
    "creative commons - public domain dedication": "CC0-1.0",
    "public domain": "CC0-1.0",
}


def _client(token: str | None) -> httpx.Client:
    headers = {"User-Agent": _UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=_BASE_URL, headers=headers, timeout=30.0, follow_redirects=True)


def _map_license(raw: str | None) -> str | None:
    if not raw:
        return None
    return _LICENSE_MAP.get(raw.strip().lower(), raw)


def _token() -> str | None:
    from app.config import get_settings
    from app.services.import_tokens import get_import_tokens_sync
    from app.tasks.base import sync_session

    settings = get_settings()
    with sync_session() as s:
        return get_import_tokens_sync(s, settings).thingiverse_token


def _username(token: str) -> str | None:
    # There's no per-user OAuth for a static app token, but the API still
    # exposes a "who am I" resource -- GET /users/me/ (Bearer) -> {"name":
    # ..., ...} -- so collections/likes (which are keyed by username, not
    # the token) can be reached without asking Settings for a second field.
    with _client(token) as c:
        r = c.get("/users/me/")
        r.raise_for_status()
        return (r.json() or {}).get("name")


class ThingiverseImporter:
    site: ClassVar[ImportSite] = ImportSite.THINGIVERSE

    def canonicalize(self, url: str) -> str | None:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None
        if host not in _ALLOWED_HOSTS:
            return None
        m = _ID_RE.search(url)
        return m.group(1) if m else None

    def _thing(self, external_id: str) -> dict:
        with _client(_token()) as c:
            r = c.get(f"/things/{external_id}")
            r.raise_for_status()
            return r.json()

    def fetch_metadata(self, external_id: str) -> ImportMetadata:
        d = self._thing(external_id)
        images = (d.get("zip_data") or {}).get("images") or []
        # T2: gallery download wants every zip_data image, cover (images[0])
        # first -- deduped since the site occasionally repeats an asset URL
        # across entries.
        image_urls: list[str] = []
        seen: set[str] = set()
        for img in images:
            url = (img or {}).get("url")
            if url and url not in seen:
                seen.add(url)
                image_urls.append(url)
        return ImportMetadata(
            site=self.site,
            external_id=str(external_id),
            source_url=f"https://www.thingiverse.com/thing:{external_id}",
            title=d.get("name") or f"thing {external_id}",
            description=d.get("description"),
            author=(d.get("creator") or {}).get("name"),
            license=_map_license(d.get("license")),
            cover_url=images[0].get("url") if images else None,
            image_urls=image_urls,
            tags=tuple(t["name"] for t in (d.get("tags") or []) if t.get("name")),
        )

    def list_files(self, external_id: str) -> list[ImportFile]:
        d = self._thing(external_id)
        # zip_data.files[] entries carry only {name, url} -- there is NO
        # download_url here (that lives on the separate GET /things/{id}/files
        # endpoint). `url` is a public cdn.thingiverse.com asset URL.
        files = (d.get("zip_data") or {}).get("files") or []
        return [
            ImportFile(
                remote_id=str(f.get("name")),
                filename=safe_filename(f.get("name")),
                url=f.get("url"),
            )
            for f in files
            if f.get("name") and f.get("url")
        ]

    def resolve_download(self, external_id: str, file: ImportFile) -> ResolvedDownload:
        # zip_data.files[].url are public cdn.thingiverse.com asset URLs -- no
        # Authorization needed, and we deliberately don't send the app token to
        # the CDN. (GET /things/{id}/files, which carries id/size/download_url
        # + a token-gated /v2/files/{id}/download, is the documented fallback.)
        return ResolvedDownload(url=file.url or "", filename=file.filename)

    def search(self, query: str, page: int = 1) -> list[SearchResult]:
        if not query:
            return []
        token = _token()
        if not token:
            # Thingiverse's search endpoint is token-gated like everything
            # else on this API -- no app token configured means no search,
            # not a crash (SPEC "importers that can't search may return []").
            return []
        with _client(token) as c:
            r = c.get(
                f"/search/{quote(query, safe='')}",
                params={"type": "things", "per_page": _SEARCH_PAGE_SIZE, "page": page},
            )
            r.raise_for_status()
            body = r.json()
        # Documented shape (developer.thingiverse.com "Search a term" --
        # GET /search/{term}), NOT captured live: no THINGIVERSE_TOKEN
        # is configured in this environment to verify against the real
        # token-gated endpoint (grounding note: "Thingiverse search
        # live-verify may be deferred if no token is configured"). The
        # documented response is `{"total": int, "hits": [...]}` where each
        # hit is a thing summary: {id, name, creator: {name}, thumbnail,
        # public_url, ...}.
        results = []
        for hit in body.get("hits") or []:
            tid = hit.get("id")
            if not tid:
                continue
            results.append(
                SearchResult(
                    site=self.site,
                    external_id=str(tid),
                    title=hit.get("name") or f"thing {tid}",
                    url=f"https://www.thingiverse.com/thing:{tid}",
                    author=(hit.get("creator") or {}).get("name"),
                    thumbnail_url=hit.get("thumbnail"),
                )
            )
        return results

    # -- saved collections / likes (M8 H) ---------------------------------
    # Official developer API, `_client(token)` Bearer seam above, all three
    # endpoints live-verified (Phase 0, app-token account @terminalfoo):
    #   GET /users/{username}/collections             -> collection objects
    #   GET /collections/{collectionId}/things?page&per_page -> thing objects
    #   GET /users/{username}/likes?page&per_page      -> thing objects (same
    #     shape as collection things) -- surfaced as a synthetic "likes" list
    #     since Thingiverse has no real collection wrapping favourites.
    # All three are bare JSON arrays (no envelope). No token configured -> []
    # (same "nothing to show, not an error" convention `search` uses).

    def list_user_lists(self) -> list[RemoteList]:
        token = _token()
        if not token:
            return []
        username = _username(token)
        if not username:
            return []
        with _client(token) as c:
            r = c.get(f"/users/{username}/collections")
            r.raise_for_status()
            body = r.json()
        lists: list[RemoteList] = []
        for coll in body:
            cid = coll.get("id")
            if not cid:
                continue
            lists.append(
                RemoteList(
                    site=self.site,
                    list_id=str(cid),
                    kind="collection",
                    title=coll.get("name") or f"collection {cid}",
                    count=coll.get("count"),
                )
            )
        lists.append(
            RemoteList(
                site=self.site, list_id="likes", kind="likes", title="Liked things", count=None
            )
        )
        return lists

    def list_list_items(self, list_id: str, page: int = 1) -> list[SearchResult]:
        token = _token()
        if not token:
            return []
        if list_id == "likes":
            username = _username(token)
            if not username:
                return []
            path = f"/users/{username}/likes"
        else:
            path = f"/collections/{list_id}/things"
        with _client(token) as c:
            r = c.get(path, params={"page": page, "per_page": _SEARCH_PAGE_SIZE})
            r.raise_for_status()
            body = r.json()
        results: list[SearchResult] = []
        for t in body:
            tid = t.get("id")
            if not tid:
                continue
            results.append(
                SearchResult(
                    site=self.site,
                    external_id=str(tid),
                    title=t.get("name") or f"thing {tid}",
                    url=t.get("public_url") or f"https://www.thingiverse.com/thing:{tid}",
                    author=(t.get("creator") or {}).get("name"),
                    thumbnail_url=t.get("thumbnail"),
                )
            )
        return results


register_importer(ThingiverseImporter())
