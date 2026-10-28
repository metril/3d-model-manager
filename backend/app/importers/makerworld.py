"""MakerWorld importer over the JSON `/api/v1` surface (Workstream B tasks
B1+B2; SPEC "Gallery importers"). ``canonicalize``/``fetch_metadata``/
``search`` work anonymously -- no Bambu account needed, which is what makes
MakerWorld useful in the search UI immediately. ``list_files``/
``resolve_download`` (the actual file download) additionally need a
Bambu-authenticated call (task B2, ``app.services.bambu_auth``): without a
connected account both still raise ``ImportRejected`` with a clear,
user-facing message; with one, they hit the authenticated download endpoint
-- see ``_fetch_authed_download_url``'s docstring for exactly how
UNVERIFIED that endpoint is. ``_client`` is the ONE httpx seam tests
monkeypatch for the anonymous calls (same contract as
``printables._client``/``thingiverse._client``); ``_authed_client`` is the
analogous seam for the Bearer-authenticated download call.

Cloudflare note (live-verified): the `/api/v1/...` JSON endpoints and the
Next.js SSR data route (`/_next/data/<buildId>/...json`) + the `/en` shell HTML
that carries the current buildId are all reachable by plain httpx with a browser
UA; Cloudflare only intermittently 403s under rapid repeat requests. `search`
uses the Next data route (see its own comment) because `/api/v1/search-service/
select/design` is a TRENDING handler that ignores the keyword entirely.

Reject-rule note (live-verified, deliberately NOT a literal reading of
"paidSetting is set" / "isExclusive is true"): a 200-design live sample
found `paidSetting` present as a non-null `{"isPaid": bool, "crowdfunding":
int}` object on EVERY design, including obviously-free ones -- it is never
omitted, so "is set" (non-None) is not a paid signal. `isExclusive` was
`true` on ~90% of that same sample; it flags MakerWorld's creator-rewards
"Exclusive" program, not a paywall (every sampled exclusive design still had
`instances[].hasZipStl: true`). Gating on either field's mere
presence/truthiness would reject nearly all MakerWorld search results,
directly defeating this task's stated goal. The actual paid signal is
`paidSetting.isPaid` (false on all 200 samples, so real paid designs are
rare, but the field is genuine) -- this mirrors Printables' `premium`
boolean check exactly, just nested one level deeper."""

from __future__ import annotations

import logging
import re
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import httpx

from app.importers.base import (
    AGGREGATE_LIST_TITLE,
    ImportFile,
    ImportMetadata,
    RemoteList,
    ResolvedDownload,
    SearchResult,
    safe_filename,
)
from app.importers.registry import register_importer
from app.models.enums import ImportSite

_BASE_URL = "https://makerworld.com/api/v1"
_ALLOWED_HOSTS = {"makerworld.com", "www.makerworld.com"}
# MakerWorld URLs: /en/models/<id>[-<slug>] (locale prefix optional/variable:
# /de/, /fr/, /zh/, /ja/, ... or none at all) -- the id is always the numeric
# segment right after "models/".
logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"models/(\d+)", re.IGNORECASE)
# Add-by-URL (M10 escape hatch B, `POST /collections/from-url`): MakerWorld
# spells the path both ways (`/collection/<id>` singular on some surfaces,
# `/collections/<id>[-slug]` plural elsewhere) -- tolerate both, plus the
# optional locale prefix `_ID_RE` above already tolerates by only searching
# rather than anchoring.
_COLLECTION_ID_RE = re.compile(r"collections?/(\d+)", re.IGNORECASE)
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)
_SEARCH_PAGE_SIZE = 20

_BAMBU_AUTH_REQUIRED = (
    "MakerWorld downloads require signing in with a Bambu account (configure in Settings)."
)
# Distinct from `_BAMBU_AUTH_REQUIRED` above: the account IS configured, but
# its stored refresh token was rejected on the last attempt
# (`app.services.bambu_auth.BambuAuthError.kind == "expired"`) -- telling the
# operator to "configure" an already-configured account is misleading (task:
# import-health truthful-failure surfacing).
_BAMBU_SESSION_EXPIRED = "Bambu sign-in expired — reconnect your Bambu account in Settings."


def parse_collection_url(url: str) -> str | None:
    """Extract a collection id from a MakerWorld collection URL for the
    add-by-URL escape hatch (M10 Workstream A, `POST /collections/from-url`)
    -- the SSR route that would otherwise let a user pick a collection off a
    list is intermittently Cloudflare-walled (see this module's docstring),
    so pasting a URL is the fallback. Tolerant of: the singular/plural path
    spelling (`/collection/<id>` vs `/collections/<id>`), an optional
    trailing `-<slug>` (MakerWorld's own share links append one, e.g.
    `/collections/18925823-esp32`), any/no locale prefix (`/en/`, `/de/`,
    ...), and a bare `?collectionId=<id>` query param instead of a path
    segment. Returns None for a non-MakerWorld host or an id-less MakerWorld
    URL -- the caller (``POST /collections/from-url``) turns that into a
    422."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        return None
    match = _COLLECTION_ID_RE.search(parsed.path)
    if match:
        return match.group(1)
    query_id = parse_qs(parsed.query).get("collectionId", [None])[0]
    if query_id and query_id.isdigit():
        return query_id
    return None


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_BASE_URL, timeout=30.0, follow_redirects=True, headers={"User-Agent": _UA}
    )


_WEB_BASE_URL = "https://makerworld.com"


def _web_client() -> httpx.Client:
    """Client rooted at the SITE (not `/api/v1`) for the Next.js SSR data route
    that backs keyword search + the buildId lookup. Separate httpx seam so tests
    can monkeypatch search independently of the `/api/v1` calls."""
    return httpx.Client(
        base_url=_WEB_BASE_URL, timeout=30.0, follow_redirects=True, headers={"User-Agent": _UA}
    )


# MakerWorld's Next.js build id changes on every deploy and namespaces the SSR
# data route (`/_next/data/<buildId>/...`). Discovered at runtime from the `/en`
# shell HTML and cached for the process; a stale id makes the data route 404,
# which `search` detects and refreshes once.
_BUILD_ID: dict[str, str] = {}


def _makerworld_build_id(client: httpx.Client, *, force: bool = False) -> str:
    if not force and _BUILD_ID.get("value"):
        return _BUILD_ID["value"]
    response = client.get("/en")
    match = re.search(r'"buildId":"([^"]+)"', response.text)
    if not match:
        challenged = (
            response.headers.get("cf-mitigated") == "challenge"
            or "Just a moment" in response.text[:200]
        )
        if challenged:
            raise RuntimeError(
                "MakerWorld is temporarily rate-limiting requests (Cloudflare challenge) -- "
                "try again shortly"
            )
        raise RuntimeError("could not determine MakerWorld build id (site markup changed?)")
    _BUILD_ID["value"] = match.group(1)
    return match.group(1)


def _authed_client(token: str, region: str = "global") -> httpx.Client:
    """The Bearer-authenticated seam (mirrors ``thingiverse._client(token)``)
    -- used ONLY by ``_fetch_authed_download_url`` for the token-gated
    download call, which lives on the Bambu account host
    (``api.bambulab.com``/``.cn``), NOT ``makerworld.com``."""
    from app.services.bambu_auth import base_url_for_region

    return httpx.Client(
        base_url=base_url_for_region(region),
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": _UA, "Authorization": f"Bearer {token}"},
    )


def _bambu_session() -> tuple[str, str]:
    """(access_token, region) for the connected Bambu account -- mirrors
    ``thingiverse._token()``'s worker-session pattern (``get_..._sync`` +
    ``sync_session``). Raises ``app.services.bambu_auth.BambuAuthError`` when
    no account is connected (or its stored refresh token can no longer
    produce an access token); the two wrappers below decide how each caller
    handles that."""
    from app.config import get_settings
    from app.services.bambu_auth import get_access_token_sync, get_bambu_auth_sync
    from app.tasks.base import sync_session

    settings = get_settings()
    with sync_session() as s:
        region = get_bambu_auth_sync(s, settings).region
        return get_access_token_sync(s, settings), region


def _require_bambu_session() -> tuple[str, str]:
    """Hard variant for ``list_files``/``resolve_download``: not being
    connected (or a dead refresh token) becomes a clear, user-facing
    ``ImportRejected`` -- the exact message depends on WHICH of those two it
    is (``BambuAuthError.kind``, task: import-health truthful-failure
    surfacing) so an operator with a configured-but-expired session isn't
    told to go configure something that's already configured."""
    from app.services.bambu_auth import BambuAuthError
    from app.tasks.importing import ImportRejected

    try:
        return _bambu_session()
    except BambuAuthError as exc:
        message = _BAMBU_SESSION_EXPIRED if exc.kind == "expired" else _BAMBU_AUTH_REQUIRED
        raise ImportRejected(message) from exc


def _favorites_client(token: str) -> httpx.Client:
    """The cookie-authenticated `/api/v1` seam for the profile/favorites reads
    below (mirrors `_client`/`_authed_client` -- a monkeypatchable module
    function tests swap independently of the anonymous `_client` and SSR
    `_web_client` seams). Headers match the live-captured browser request
    (mw_capture_notes.md's grounding); the `token` cookie is the actual auth."""
    return httpx.Client(
        base_url=_BASE_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "x-bbl-app-source": "makerworld",
            "x-bbl-client-name": "MakerWorld",
            "x-bbl-client-type": "web",
            "x-bbl-client-version": "00.00.00.01",
            "Cookie": f"token={token}",
        },
    )


def _makerworld_web_token() -> str | None:
    """The makerworld.com web `token` cookie, user-supplied in Settings.

    NOT the Bambu access token: the web cookie is opaque (`AACB…`) while
    Bambu issues JWTs (live-captured 2026-07-10). Reads are silently empty
    rather than 401 when unauthenticated (HTTP 200 `{"hits":[],"total":0}`),
    so a missing token must degrade to "nothing to show", never to an error.
    """
    from app.config import get_settings
    from app.services.import_tokens import get_import_tokens_sync
    from app.tasks.base import sync_session

    settings = get_settings()
    with sync_session() as s:
        return get_import_tokens_sync(s, settings).makerworld_token


def _profile(token: str) -> tuple[int, str]:
    """`(uid, handle)` for the connected MakerWorld account (VERIFIED:
    `GET /user-service/my/profile` -> `{"uid": ..., "name": ...}`). `uid` also
    doubles as the id MakerWorld itself uses for the aggregate "all collected
    models" list; `handle` (the account's `name`, e.g. "Terminalfoo") is what
    the favorites/collections endpoints below expect as `@{handle}`."""
    with _favorites_client(token) as c:
        r = c.get("/user-service/my/profile")
        r.raise_for_status()
        body = r.json()
    return int(body["uid"]), str(body["name"])


def _image_urls(d: dict) -> list[str]:
    """Cover-first, deduped gallery picture list (T2) for the design JSON
    ``d``. There is no single design-level "designPictures"/"modelPictures"
    field on the real payload (grounding probe design 3018898, mirrored in
    ``tests/cassettes/makerworld_fixtures.py``) -- the only actual picture
    list is each ``instances[].pictures[].url`` (per-print-profile photos).
    Falls back to just ``[coverUrl]`` when no instance carries pictures."""
    urls: list[str] = []
    seen: set[str] = set()
    cover = d.get("coverUrl")
    if cover:
        urls.append(cover)
        seen.add(cover)
    for inst in d.get("instances") or []:
        for pic in inst.get("pictures") or []:
            url = (pic or {}).get("url")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


class MakerWorldImporter:
    site: ClassVar[ImportSite] = ImportSite.MAKERWORLD

    def canonicalize(self, url: str) -> str | None:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None
        if host not in _ALLOWED_HOSTS:
            return None
        m = _ID_RE.search(url)
        return m.group(1) if m else None

    def _design(self, external_id: str) -> dict:
        with _client() as c:
            r = c.get(f"/design-service/design/{external_id}")
            r.raise_for_status()
            return r.json()

    def fetch_metadata(self, external_id: str) -> ImportMetadata:
        d = self._design(external_id)
        reject = None
        if (d.get("paidSetting") or {}).get("isPaid"):
            reject = "This is a paid MakerWorld model and can't be imported (login required)."
        return ImportMetadata(
            site=self.site,
            external_id=str(external_id),
            source_url=f"https://www.makerworld.com/en/models/{external_id}",
            title=d.get("title") or f"model {external_id}",
            description=d.get("summary"),
            author=(d.get("designCreator") or {}).get("name"),
            license=d.get("license"),
            cover_url=d.get("coverUrl"),
            image_urls=_image_urls(d),
            tags=tuple(t for t in d.get("tags", []) if t),
            reject_reason=reject,
        )

    def list_files(self, external_id: str) -> list[ImportFile]:
        # Bambu-authenticated download (B2). MakerWorld's design/instance
        # JSON carries no downloadable-file URL anonymously (only cover/
        # plate thumbnails) -- `hasZipStl: true` on an instance just means a
        # download EXISTS, not that we can reach it without a Bambu Bearer
        # token. `_require_bambu_session` turns "not connected" into the
        # same clear, user-facing rejection the B1 stub raised
        # unconditionally (never a crash, never a silent "no files found"
        # which would misreport WHY).
        _require_bambu_session()
        d = self._design(external_id)
        title = d.get("title") or f"model {external_id}"
        files = []
        for inst in d.get("instances", []):
            if not inst.get("hasZipStl"):
                continue
            profile_id = inst.get("profileId")
            if not profile_id:
                continue
            name = inst.get("title") or title
            filename = safe_filename(f"{name}-{inst.get('id')}.zip")
            files.append(ImportFile(remote_id=str(profile_id), filename=filename))
        return files

    def resolve_download(self, external_id: str, file: ImportFile) -> ResolvedDownload:
        token, region = _require_bambu_session()
        # The download endpoint (see `_fetch_authed_download_url`) is keyed
        # on `profileId` (carried through as `file.remote_id`) + the
        # design's `modelId` string -- re-fetch the (anonymous, cheap)
        # design detail rather than widening `ImportFile` with a MakerWorld-
        # specific field just to carry `modelId` from `list_files` to here.
        d = self._design(external_id)
        url = self._fetch_authed_download_url(
            profile_id=file.remote_id, model_id=d.get("modelId"), token=token, region=region
        )
        return ResolvedDownload(url=url, filename=file.filename)

    # === UNVERIFIED: Bambu-authenticated download endpoint (B2) ===========
    def _fetch_authed_download_url(
        self, *, profile_id: str, model_id: str | None, token: str, region: str
    ) -> str:
        """ISOLATED on purpose: this is the ONE piece of the importer that
        could NOT be live-verified (grounding probe: every anonymous
        candidate path 404s, and this one requires a real logged-in Bambu
        token). It targets the endpoint documented in this project's own
        approved design spec (``docs/superpowers/specs/2026-07-04-3d-model-
        manager-design-full.md`` line 230): ``GET /v1/iot-service/api/user/
        profile/{profileId}?model_id=<id>`` on the Bambu account host
        (``api.bambulab.com``/``.cn`` -- the SAME host that issues the
        Bearer token, not ``makerworld.com``), returning a short-TTL (~5 min)
        presigned S3 file URL that must be fetched immediately, never
        cached/normalized.

        NEITHER the exact response field carrying that URL NOR whether
        ``model_id`` wants the design's numeric ``id`` or its ``modelId``
        string (used here, since the query-param name matches that JSON key
        literally) was ever captured against a real Bambu account -- this
        MUST be re-verified against a live connected account before it's
        trusted in production. If the real shape differs, only this method
        (and the field-name tolerance list below) should need adjusting.
        """
        with _authed_client(token, region) as c:
            r = c.get(
                f"/iot-service/api/user/profile/{profile_id}",
                params={"model_id": model_id or ""},
            )
            r.raise_for_status()
            body = r.json()
        candidates = (body, body.get("data") if isinstance(body.get("data"), dict) else None)
        url_keys = (
            "url",
            "presignedUrl",
            "presigned_url",
            "downloadUrl",
            "download_url",
            "fileUrl",
        )
        for candidate in candidates:
            if not candidate:
                continue
            for key in url_keys:
                value = candidate.get(key)
                if value:
                    return value
        from app.tasks.importing import ImportRejected

        raise ImportRejected(
            "MakerWorld's authenticated download response didn't include a recognizable "
            "file URL (the endpoint's shape may have changed -- see "
            "MakerWorldImporter._fetch_authed_download_url)."
        )

    # ========================================================================

    def search(self, query: str, page: int = 1) -> list[SearchResult]:
        if not query:
            return []
        offset = max(page - 1, 0) * _SEARCH_PAGE_SIZE
        # MakerWorld's REAL keyword search is its Next.js SSR data route
        # (`/_next/data/<buildId>/en/search/models.json?keyword=`), reached
        # ANONYMOUSLY -- live-captured from the site. The `/api/v1/search-service/
        # select/design` endpoint the first cut used is a trending/browse handler
        # that ignores the keyword entirely (verified: `q`/`query`/`keyword`/
        # `sort=score`/`q=title:benchy` all return the identical top list), and
        # the Bambu Bearer it attached was for `api.bambulab.com`, never honored
        # here. Results live at `pageProps.designs`.
        with _web_client() as c:
            build_id = _makerworld_build_id(c)
            response = self._search_page(c, build_id, query, offset)
            if response.status_code == 404:
                # A deploy since we cached the buildId -> refresh once and retry.
                build_id = _makerworld_build_id(c, force=True)
                response = self._search_page(c, build_id, query, offset)
            response.raise_for_status()
            designs = ((response.json() or {}).get("pageProps") or {}).get("designs") or []
        results = []
        for design in designs:
            mid = design.get("id")
            if not mid:
                continue
            results.append(
                SearchResult(
                    site=self.site,
                    external_id=str(mid),
                    title=design.get("title") or f"model {mid}",
                    url=f"https://www.makerworld.com/en/models/{mid}",
                    author=(design.get("designCreator") or {}).get("name"),
                    thumbnail_url=design.get("cover"),
                )
            )
        return results

    @staticmethod
    def _search_page(
        client: httpx.Client, build_id: str, query: str, offset: int
    ) -> httpx.Response:
        return client.get(
            f"/_next/data/{build_id}/en/search/models.json",
            params={"keyword": query, "offset": offset, "limit": _SEARCH_PAGE_SIZE},
            headers={"x-nextjs-data": "1"},
        )

    # -- saved collections / likes (M8 H) ---------------------------------
    # Auth is `_makerworld_web_token()` -- a user-pasted MakerWorld web
    # cookie stored in Settings (task A5), not the Bambu account. Both
    # methods return [] when no token is stored -- the "nothing to show, not
    # an error" convention `search` uses.

    def list_user_lists(self) -> list[RemoteList]:
        # Deferred (mirrors `_bambu_session`/`_makerworld_web_token` above):
        # `app.services.remote_collections`/`app.tasks.base` pull in the
        # worker-side sync DB stack, which importer modules otherwise have no
        # reason to import at module load time.
        from app.services.remote_collections import (
            CacheEntry,
            get_site_cache,
            replace_site_cache_sync,
        )
        from app.tasks.base import sync_session

        token = _makerworld_web_token()
        if not token:
            return []
        uid, handle = _profile(token)
        # Always emit the aggregate list -- it works headlessly off just the
        # uid (VERIFIED: `{listId}` = uid -> "all collected models", total
        # 101 for @Terminalfoo -- mw_capture_notes.md). `count=None` since
        # getting a true total here means an extra items-page fetch just for
        # a number the UI can compute once it lists items anyway.
        lists = [
            RemoteList(
                site=self.site,
                list_id=str(uid),
                kind="collection",
                title=AGGREGATE_LIST_TITLE,
                count=None,
            )
        ]
        # BEST-EFFORT named collections: the SSR `collections.json` route is
        # intermittently Cloudflare-walled from a server IP (unlike the items
        # endpoint above, which isn't) -- a failure here must not take down
        # the aggregate list already built.
        try:
            with _web_client() as c:
                c.cookies.set("token", token)
                build_id = _makerworld_build_id(c)
                response = self._collections_page(c, build_id, handle)
                if response.status_code == 404:
                    build_id = _makerworld_build_id(c, force=True)
                    response = self._collections_page(c, build_id, handle)
                response.raise_for_status()
                page_props = (response.json() or {}).get("pageProps") or {}
                favorites = page_props.get("favoritesList") or []
            cache_entries: list[CacheEntry] = []
            for coll in favorites:
                if coll.get("status") != 1:
                    continue
                cid = coll.get("id")
                if not cid:
                    continue
                lists.append(
                    RemoteList(
                        site=self.site,
                        list_id=str(cid),
                        kind="collection",
                        title=coll.get("title") or f"collection {cid}",
                        count=coll.get("designCnt"),
                    )
                )
                cache_entries.append(
                    CacheEntry(
                        list_id=str(cid),
                        title=coll.get("title") or f"collection {cid}",
                        slug=coll.get("slug"),
                        count=coll.get("designCnt"),
                        is_default=bool(coll.get("isDefault")),
                    )
                )
            # SSR succeeded -- self-heal the cache (M10 escape hatch A) so it
            # stays warm even when the extension hasn't pushed lately. Only
            # write when there's something to write: an SSR 200 with an
            # empty/shape-drifted `favoritesList` would otherwise call
            # `replace_site_cache_sync` with `[]`, which is a full-replace
            # and would wipe every collection the extension already pushed.
            # The extension's own empty push (`POST /ext/collections` with
            # `collections: []`) stays the one authoritative way to clear the
            # cache.
            if cache_entries:
                with sync_session() as s:
                    replace_site_cache_sync(s, self.site, cache_entries)
        except (RuntimeError, httpx.HTTPError, KeyError) as exc:
            # RuntimeError: Cloudflare challenge (`_makerworld_build_id`).
            # httpx.HTTPError: non-2xx (`raise_for_status`) / transport error.
            # KeyError: unexpected pageProps shape. Any of these -> skip the
            # named collections (and the cache write above) -- the merge
            # below fills the gap from whatever is already cached.
            logger.warning("makerworld SSR collections listing failed: %r", exc)
        # ALWAYS merge cached rows in (M10 escape hatch A): the extension's
        # push -- or a past successful SSR read -- fills in what THIS call's
        # SSR attempt couldn't reach. An SSR-fresh entry above wins over the
        # cache for the same id; a cached row equal to the uid would just
        # duplicate the aggregate already at the front of `lists`.
        seen = {entry.list_id for entry in lists}
        with sync_session() as s:
            cached = get_site_cache(s, self.site)
        for row in cached:
            if row.list_id == str(uid) or row.list_id in seen:
                continue
            lists.append(
                RemoteList(
                    site=self.site,
                    list_id=row.list_id,
                    kind="collection",
                    title=row.title,
                    count=row.count,
                )
            )
        return lists

    @staticmethod
    def _collections_page(client: httpx.Client, build_id: str, handle: str) -> httpx.Response:
        # `token` rides along as a client-level cookie (set by the caller
        # just above) rather than a per-request one -- httpx deprecated the
        # latter, and the M1 gates require warning-free pytest output.
        return client.get(
            f"/_next/data/{build_id}/en/@{handle}/collections.json",
            params={"handle": f"@{handle}"},
            headers={"x-nextjs-data": "1"},
        )

    def list_list_items(self, list_id: str, page: int = 1) -> list[SearchResult]:
        token = _makerworld_web_token()
        if not token:
            return []
        uid, handle = _profile(token)
        offset = max(page - 1, 0) * _SEARCH_PAGE_SIZE
        # LIVE-VERIFIED (2026-07-11, corrects an earlier "VERIFIED ... named
        # collection id" comment that was wrong): this endpoint serves ONLY
        # the uid aggregate ("all collected models") from a server IP -- a
        # real named collection id comes back `200 {"total":0}` even though
        # the collection genuinely has items (checked against 3 real ids).
        # Still attempted first every time (a live hit always wins -- if
        # MakerWorld ever fixes this server-side, nothing here needs to
        # change), but a named collection (list_id != uid) that comes back
        # empty falls back to `remote_collection_items` below: the browser
        # extension's own push from the user's authenticated browser, where
        # the wall around this endpoint isn't up (see
        # `app.models.collections.RemoteCollectionItem`'s docstring).
        with _favorites_client(token) as c:
            r = c.get(
                f"/design-service/favorites/designs/{list_id}",
                params={"handle": f"@{handle}", "limit": _SEARCH_PAGE_SIZE, "offset": offset},
            )
            r.raise_for_status()
            hits = (r.json() or {}).get("hits") or []
        results: list[SearchResult] = []
        for h in hits:
            hid = h.get("id")
            if not hid:
                continue
            results.append(
                SearchResult(
                    site=self.site,
                    external_id=str(hid),
                    title=h.get("title") or f"model {hid}",
                    url=f"https://www.makerworld.com/en/models/{hid}",
                    author=(h.get("designCreator") or {}).get("name"),
                    thumbnail_url=h.get("cover"),
                )
            )
        if results or list_id == str(uid):
            return results
        return self._cached_list_items(list_id, page)

    def _cached_list_items(self, list_id: str, page: int) -> list[SearchResult]:
        # Deferred (mirrors `list_user_lists` above): pulls in the
        # worker-side sync DB stack only when the live fetch actually came
        # back empty for a named list.
        from app.services.remote_collections import get_list_items
        from app.tasks.base import sync_session

        with sync_session() as s:
            cached = get_list_items(s, self.site, list_id)
        start = max(page - 1, 0) * _SEARCH_PAGE_SIZE
        return [
            SearchResult(
                site=self.site,
                external_id=row.external_id,
                title=row.title,
                url=row.url,
                author=row.author,
                thumbnail_url=row.thumbnail_url,
            )
            for row in cached[start : start + _SEARCH_PAGE_SIZE]
        ]


register_importer(MakerWorldImporter())
