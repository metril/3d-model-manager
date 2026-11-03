import contextlib

import httpx
import pytest

from app.importers import makerworld
from app.importers.base import ImportFile
from app.importers.makerworld import MakerWorldImporter
from app.models.enums import ImportSite
from app.services import import_tokens as import_tokens_service
from app.services import remote_collections as remote_collections_service
from app.services.bambu_auth import BambuAuthError
from app.tasks import base as tasks_base
from app.tasks.importing import ImportRejected
from tests.cassettes import makerworld_fixtures as fx


@pytest.fixture(autouse=True)
def _truncate_all_tables():
    """Local no-op override of the suite-wide autouse DB-truncate fixture
    (conftest.py) -- every test in this module is DB-free (pure HTTP-mock /
    string parsing), same override as test_thingiverse_importer.py (M6 C3c)."""
    yield


def _mock_design_client(body, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.Client(
        base_url="https://makerworld.com/api/v1", transport=httpx.MockTransport(handler)
    )


def _mock_web_client(designs, *, build_id="TESTBUILD123", stale_id=None):
    """Mock the Next.js SSR search seam: `/en` yields a buildId, and
    `/_next/data/<buildId>/en/search/models.json` returns pageProps.designs.
    Passing `stale_id` makes the data route 404 for THAT id once (a deploy
    since the id was cached), exercising the refresh-and-retry path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/en":
            return httpx.Response(200, text=f'<script>{{"buildId":"{build_id}"}}</script>')
        if path.startswith("/_next/data/") and path.endswith("/en/search/models.json"):
            used = path.split("/_next/data/", 1)[1].split("/", 1)[0]
            params = dict(request.url.params)
            assert params["keyword"] == fx.SEARCH_QUERY
            assert params["offset"] == "0" and params["limit"] == "20"
            if stale_id is not None and used == stale_id:
                return httpx.Response(404, json={"pageProps": {}})
            return httpx.Response(200, json={"pageProps": {"designs": designs, "total": 3545}})
        return httpx.Response(404, text="unexpected path")

    return httpx.Client(base_url="https://makerworld.com", transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _clear_build_id_cache():
    makerworld._BUILD_ID.clear()
    yield
    makerworld._BUILD_ID.clear()


@pytest.fixture
def _cache_db(migrated_db):
    """`list_user_lists` now reads/writes `remote_collection_cache` (M10
    escape hatch A) even though this whole module is otherwise DB-free (see
    the no-op `_truncate_all_tables` override above). Tests that exercise
    that need the real migrated DB -- truncate just this one table around
    them rather than lifting the module-wide DB-free override."""
    from sqlalchemy import text

    def _truncate() -> None:
        with tasks_base.sync_session() as s:
            s.execute(text("TRUNCATE TABLE remote_collection_cache RESTART IDENTITY CASCADE"))
            s.commit()

    _truncate()
    yield
    _truncate()


@pytest.fixture
def _items_db(migrated_db):
    """`list_list_items`'s cache fallback (task 3) reads `remote_collection_
    items` -- same posture as `_cache_db` above, but for the sibling table."""
    from sqlalchemy import text

    def _truncate() -> None:
        with tasks_base.sync_session() as s:
            s.execute(text("TRUNCATE TABLE remote_collection_items RESTART IDENTITY CASCADE"))
            s.commit()

    _truncate()
    yield
    _truncate()


def _raise_not_connected():
    raise BambuAuthError(
        "no Bambu account is connected -- connect one in Settings.", kind="not_configured"
    )


def _raise_expired():
    raise BambuAuthError(
        "Bambu refresh token is invalid or expired -- reconnect the Bambu account in Settings.",
        kind="expired",
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://makerworld.com/en/models/3018898-nasa-fabric-pokeball", "3018898"),
        ("https://www.makerworld.com/en/models/3018898", "3018898"),
        ("https://makerworld.com/de/models/42", "42"),
        ("https://makerworld.com/models/42", "42"),
        ("https://www.thingiverse.com/thing:1", None),
        ("https://makerworld.com/en/collections/17685211-x", None),
    ],
)
def test_canonicalize(url, expected):
    assert MakerWorldImporter().canonicalize(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://makerworld.com/en/collections/18925823-esp32", "18925823"),
        ("https://makerworld.com/collections/18925823-esp32", "18925823"),
        ("https://makerworld.com/collections/18925823", "18925823"),
        ("https://www.makerworld.com/collections/18925823", "18925823"),
        ("https://www.makerworld.com/en/collections/18925823", "18925823"),
        ("https://makerworld.com/collection/18925823", "18925823"),
        ("https://www.makerworld.com/en/collection/18925823", "18925823"),
        ("https://makerworld.com/en/@Terminalfoo?collectionId=18925823", "18925823"),
        ("https://www.makerworld.com/@Terminalfoo?collectionId=18925823", "18925823"),
        ("https://www.thingiverse.com/collections/18925823", None),
        ("https://makerworld.com/en/models/3018898", None),
        ("https://makerworld.com/en/collections/", None),
    ],
)
def test_parse_collection_url(url, expected):
    assert makerworld.parse_collection_url(url) == expected


def test_fetch_metadata_maps_verified_fields(monkeypatch):
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(fx.DESIGN_3018898))
    meta = MakerWorldImporter().fetch_metadata(fx.DESIGN_ID)
    assert meta.site is ImportSite.MAKERWORLD and meta.external_id == fx.DESIGN_ID
    assert meta.title == "NASA Fabric: Pokeball (No AMS Needed)"
    assert meta.source_url == "https://www.makerworld.com/en/models/3018898"
    assert meta.author == "MeasureOnce"
    assert meta.license == "Standard Digital File License"
    assert meta.cover_url == fx.DESIGN_3018898["coverUrl"]
    assert meta.description == fx.DESIGN_3018898["summary"]
    assert set(meta.tags) == set(fx.DESIGN_3018898["tags"])
    # isExclusive is True on this fixture (live-verified: ~90% of MakerWorld
    # designs are, unrelated to payment -- see makerworld.py's docstring) and
    # paidSetting.isPaid is False -- must NOT be rejected.
    assert meta.reject_reason is None


def test_fetch_metadata_image_urls_are_cover_first_and_deduped(monkeypatch):
    # DESIGN_3018898's one instance carries a "pictures" list distinct from
    # the design-level "coverUrl" -- image_urls must lead with coverUrl, then
    # append the instance picture(s), with no duplicates.
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(fx.DESIGN_3018898))
    meta = MakerWorldImporter().fetch_metadata(fx.DESIGN_ID)
    assert meta.image_urls == [
        fx.DESIGN_3018898["coverUrl"],
        fx.DESIGN_3018898["instances"][0]["pictures"][0]["url"],
    ]


def test_fetch_metadata_image_urls_falls_back_to_cover_alone_without_instances(monkeypatch):
    design = {**fx.DESIGN_3018898, "instances": []}
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(design))
    meta = MakerWorldImporter().fetch_metadata(fx.DESIGN_ID)
    assert meta.image_urls == [fx.DESIGN_3018898["coverUrl"]]


def test_fetch_metadata_image_urls_dedupes_a_picture_matching_the_cover(monkeypatch):
    design = {
        **fx.DESIGN_3018898,
        "instances": [
            {
                **fx.DESIGN_3018898["instances"][0],
                "pictures": [{"url": fx.DESIGN_3018898["coverUrl"]}],
            }
        ],
    }
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(design))
    meta = MakerWorldImporter().fetch_metadata(fx.DESIGN_ID)
    assert meta.image_urls == [fx.DESIGN_3018898["coverUrl"]]


def test_paid_model_is_rejected_with_clear_message(monkeypatch):
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(fx.DESIGN_PAID))
    meta = MakerWorldImporter().fetch_metadata(str(fx.DESIGN_PAID["id"]))
    assert meta.reject_reason and "paid" in meta.reject_reason.lower()


def test_search_maps_designs_to_search_results(monkeypatch):
    # Anonymous (no Bambu account, no DB) -- the Next.js data route needs none.
    monkeypatch.setattr(
        makerworld, "_web_client", lambda: _mock_web_client(fx.SEARCH_DESIGNS_BENCHY)
    )
    results = MakerWorldImporter().search(fx.SEARCH_QUERY)
    assert [r.title for r in results] == [
        "NASA Fabric: Pokeball (No AMS Needed)",
        "12-in-1 Ultimate Multi Fidget Toy (Print in Place)",
    ]
    first = results[0]
    assert first.site is ImportSite.MAKERWORLD and first.external_id == "3018898"
    assert first.url == "https://www.makerworld.com/en/models/3018898"
    assert first.author == "MeasureOnce"
    assert first.thumbnail_url == fx.SEARCH_DESIGNS_BENCHY[0]["cover"]


def test_search_refreshes_a_stale_build_id(monkeypatch):
    # A deploy since the id was cached: the data route 404s on the stale id,
    # and search must re-read /en for the fresh one and retry (not just fail).
    makerworld._BUILD_ID["value"] = "STALEBUILD"
    monkeypatch.setattr(
        makerworld,
        "_web_client",
        lambda: _mock_web_client(
            fx.SEARCH_DESIGNS_BENCHY, build_id="FRESHBUILD", stale_id="STALEBUILD"
        ),
    )
    results = MakerWorldImporter().search(fx.SEARCH_QUERY)
    assert [r.external_id for r in results] == ["3018898", "3012887"]


def test_search_surfaces_a_cloudflare_challenge_clearly(monkeypatch):
    def _challenged():
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403, headers={"cf-mitigated": "challenge"}, text="<title>Just a moment...</title>"
            )

        return httpx.Client(
            base_url="https://makerworld.com", transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(makerworld, "_web_client", _challenged)
    with pytest.raises(RuntimeError, match="rate-limiting"):
        MakerWorldImporter().search(fx.SEARCH_QUERY)


def test_search_empty_query_returns_empty_list():
    assert MakerWorldImporter().search("") == []


def test_list_files_raises_bambu_auth_required_when_not_connected(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: _raise_not_connected())
    with pytest.raises(ImportRejected, match="Bambu") as exc_info:
        MakerWorldImporter().list_files(fx.DESIGN_ID)
    # "Not configured" keeps the original "configure in Settings" wording --
    # NOT the "sign-in expired" one (task: import-health truthful failure).
    assert str(exc_info.value) == makerworld._BAMBU_AUTH_REQUIRED
    assert "expired" not in str(exc_info.value)


def test_resolve_download_raises_bambu_auth_required_when_not_connected(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: _raise_not_connected())
    with pytest.raises(ImportRejected, match="Bambu") as exc_info:
        MakerWorldImporter().resolve_download(
            fx.DESIGN_ID, ImportFile(remote_id="x", filename="x.stl")
        )
    assert str(exc_info.value) == makerworld._BAMBU_AUTH_REQUIRED


def test_list_files_raises_session_expired_message_when_refresh_failed(monkeypatch):
    # The live-evidence scenario this task exists for: the account IS
    # configured, but its stored refresh token 401s -- the message must say
    # so, not misleadingly tell the operator to "configure" a Bambu account
    # they already configured.
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: _raise_expired())
    with pytest.raises(ImportRejected) as exc_info:
        MakerWorldImporter().list_files(fx.DESIGN_ID)
    assert str(exc_info.value) == makerworld._BAMBU_SESSION_EXPIRED
    assert str(exc_info.value) != makerworld._BAMBU_AUTH_REQUIRED
    assert "configure in Settings" not in str(exc_info.value)


def test_resolve_download_raises_session_expired_message_when_refresh_failed(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: _raise_expired())
    with pytest.raises(ImportRejected) as exc_info:
        MakerWorldImporter().resolve_download(
            fx.DESIGN_ID, ImportFile(remote_id="x", filename="x.stl")
        )
    assert str(exc_info.value) == makerworld._BAMBU_SESSION_EXPIRED


def test_list_files_with_connected_account_maps_zip_stl_instances(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("test-access-token", "global"))
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(fx.DESIGN_3018898))
    files = MakerWorldImporter().list_files(fx.DESIGN_ID)
    assert len(files) == 1
    # profileId from the one hasZipStl instance in the fixture.
    assert files[0].remote_id == "12345"
    assert files[0].filename == "Default-3391581.zip"


def test_fetch_metadata_tolerates_null_tags(monkeypatch):
    design = {**fx.DESIGN_3018898, "tags": None}
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(design))
    assert MakerWorldImporter().fetch_metadata(fx.DESIGN_ID).tags == ()


def test_list_files_tolerates_null_instances(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("test-access-token", "global"))
    design = {**fx.DESIGN_3018898, "instances": None}
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(design))
    # No crash; null instances behaves like none -> the raw-model fallback.
    files = MakerWorldImporter().list_files(fx.DESIGN_ID)
    assert [f.remote_id for f in files] == ["raw"]


def test_list_files_emits_raw_zip_for_design_without_instances(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("test-access-token", "global"))
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(fx.DESIGN_RAW_ONLY))
    files = MakerWorldImporter().list_files("541281")
    assert files == [ImportFile(remote_id="raw", filename="PCIe 92mm fan bracket v3-541281.zip")]


def test_list_files_prefers_instances_over_raw_zip(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("test-access-token", "global"))
    design = {**fx.DESIGN_3018898, "rawModelFileDownloadCount": 50}
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(design))
    files = MakerWorldImporter().list_files(fx.DESIGN_ID)
    assert [f.remote_id for f in files] == ["12345"]


def _raw_model_client_for(status_code, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/design-service/design/541281/model"
        assert dict(request.url.params) == {"modelType": "all", "type": "download"}
        assert request.headers.get("Authorization") == "Bearer test-access-token"
        return httpx.Response(status_code, json=body or {})

    return lambda token: httpx.Client(
        base_url="https://makerworld.com/api/v1",
        headers={"Authorization": f"Bearer {token}"},
        transport=httpx.MockTransport(handler),
    )


def test_resolve_download_raw_hits_model_endpoint_and_returns_url(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("test-access-token", "global"))
    monkeypatch.setattr(
        makerworld, "_raw_model_client", _raw_model_client_for(200, fx.RAW_MODEL_RESPONSE)
    )
    out = MakerWorldImporter().resolve_download(
        "541281", ImportFile(remote_id="raw", filename="PCIe-541281.zip")
    )
    assert out.url == fx.RAW_MODEL_RESPONSE["url"]
    assert out.filename == "PCIe-541281.zip"  # always ours, never the response `name`
    assert out.headers == {}


def test_resolve_download_raw_forbidden_is_rejected(monkeypatch):
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("test-access-token", "global"))
    monkeypatch.setattr(makerworld, "_raw_model_client", _raw_model_client_for(403))
    with pytest.raises(ImportRejected) as exc_info:
        MakerWorldImporter().resolve_download(
            "541281", ImportFile(remote_id="raw", filename="x.zip")
        )
    assert str(exc_info.value) == makerworld._BAMBU_SESSION_EXPIRED


def _mock_favorites_client(token, *, profile=None, hits_by_list=None):
    """Mock the cookie-authed `/api/v1` seam `_favorites_client` builds:
    handles `/user-service/my/profile` and `/design-service/favorites/
    designs/{listId}`, asserting the `token` cookie and `@{handle}` param
    both callers below are expected to send."""
    profile = profile or fx.PROFILE_TERMINALFOO
    hits_by_list = hits_by_list or {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Cookie") == f"token={token}"
        path = request.url.path
        if path == "/api/v1/user-service/my/profile":
            return httpx.Response(200, json=profile)
        if path.startswith("/api/v1/design-service/favorites/designs/"):
            list_id = path.rsplit("/", 1)[-1]
            params = dict(request.url.params)
            assert params.get("handle") == f"@{profile['name']}"
            hits = hits_by_list.get(list_id, [])
            return httpx.Response(200, json={"hits": hits, "total": len(hits), "seed": 1})
        return httpx.Response(404, text="unexpected path")

    return httpx.Client(
        base_url="https://makerworld.com/api/v1",
        headers={"Cookie": f"token={token}"},
        transport=httpx.MockTransport(handler),
    )


def _mock_collections_client(
    favorites_list, *, build_id="TESTBUILD123", handle="Terminalfoo", token="test-token"
):
    """Mock the SSR `collections.json` seam `list_user_lists` reads via
    `_web_client`: `/en` yields a buildId, and `/_next/data/<buildId>/en/
    @<handle>/collections.json` returns `pageProps.favoritesList`."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/en":
            return httpx.Response(200, text=f'<script>{{"buildId":"{build_id}"}}</script>')
        if path == f"/_next/data/{build_id}/en/@{handle}/collections.json":
            assert dict(request.url.params) == {"handle": f"@{handle}"}
            assert request.headers.get("x-nextjs-data") == "1"
            assert request.headers.get("Cookie") == f"token={token}"
            return httpx.Response(200, json={"pageProps": {"favoritesList": favorites_list}})
        return httpx.Response(404, text="unexpected path")

    return httpx.Client(base_url="https://makerworld.com", transport=httpx.MockTransport(handler))


def _challenged_web_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, headers={"cf-mitigated": "challenge"}, text="<title>Just a moment...</title>"
        )

    return httpx.Client(base_url="https://makerworld.com", transport=httpx.MockTransport(handler))


def test_list_user_lists_none_when_no_token_stored(monkeypatch):
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: None)
    assert MakerWorldImporter().list_user_lists() == []


def test_list_list_items_none_when_no_token_stored(monkeypatch):
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: None)
    assert MakerWorldImporter().list_list_items("3054026541") == []


def _forbidden_client(*args, **kwargs):
    raise AssertionError("must not make an HTTP call when no MakerWorld token is stored")


def test_list_user_lists_makes_no_http_call_when_no_token_stored(monkeypatch):
    # A missing token must short-circuit BEFORE touching either httpx seam
    # `list_user_lists` otherwise uses (`_favorites_client` for `_profile`,
    # `_web_client` for the named-collections SSR route) -- a wrong/expired
    # token silently comes back HTTP 200 `{"hits":[],"total":0}`
    # (task A5), so this must never even attempt the call.
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: None)
    monkeypatch.setattr(makerworld, "_favorites_client", _forbidden_client)
    monkeypatch.setattr(makerworld, "_web_client", _forbidden_client)
    assert MakerWorldImporter().list_user_lists() == []


def test_list_list_items_makes_no_http_call_when_no_token_stored(monkeypatch):
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: None)
    monkeypatch.setattr(makerworld, "_favorites_client", _forbidden_client)
    assert MakerWorldImporter().list_list_items("3054026541") == []


def test_makerworld_web_token_reads_stored_token_not_bambu(monkeypatch):
    """`_makerworld_web_token` reads the user-pasted MakerWorld web token via
    the import_tokens service (task A5) -- NOT the Bambu account. Monkeypatch
    the token-service seam directly (not `_bambu_session`) so this stays a
    DB-free test like the rest of this module."""
    monkeypatch.setattr(tasks_base, "sync_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        import_tokens_service,
        "get_import_tokens_sync",
        lambda session, settings: import_tokens_service.ImportTokens(
            makerworld_token="AACB-stored-web-token"
        ),
    )
    assert makerworld._makerworld_web_token() == "AACB-stored-web-token"


def test_makerworld_web_token_none_when_nothing_stored(monkeypatch):
    monkeypatch.setattr(tasks_base, "sync_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        import_tokens_service,
        "get_import_tokens_sync",
        lambda session, settings: import_tokens_service.ImportTokens(),
    )
    assert makerworld._makerworld_web_token() is None


def test_list_user_lists_returns_aggregate_and_named_collections(monkeypatch, _cache_db):
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld, "_favorites_client", lambda token: _mock_favorites_client(token)
    )
    monkeypatch.setattr(
        makerworld, "_web_client", lambda: _mock_collections_client(fx.FAVORITES_LIST)
    )
    lists = MakerWorldImporter().list_user_lists()
    # Aggregate first, keyed by uid from the (consulted) profile.
    aggregate = lists[0]
    assert aggregate.list_id == str(fx.PROFILE_TERMINALFOO["uid"])
    assert aggregate.kind == "collection"
    assert aggregate.title == "All collected models"
    assert aggregate.count is None
    # Named collections: title/count mapped, `status: 2` ("Trays") skipped.
    named = {entry.list_id: entry for entry in lists[1:]}
    assert set(named) == {"2155987", "18925823"}
    assert named["2155987"].title == "Default Collection"
    assert named["2155987"].count == 7
    assert named["18925823"].title == "ESP32"
    assert named["18925823"].count == 9

    # SSR succeeded -- self-heals the cache (M10 escape hatch A) so it's warm
    # the next time the wall is up. "Trays" (status 2) stays excluded, same
    # as from the returned list above.
    with tasks_base.sync_session() as s:
        cached = {
            row.list_id: row
            for row in remote_collections_service.get_site_cache(s, ImportSite.MAKERWORLD)
        }
    assert set(cached) == {"2155987", "18925823"}
    assert cached["2155987"].title == "Default Collection" and cached["2155987"].is_default is True
    assert cached["18925823"].title == "ESP32" and cached["18925823"].is_default is False


def test_list_user_lists_ssr_success_with_empty_favorites_list_does_not_wipe_warm_cache(
    monkeypatch, _cache_db
):
    """An SSR 200 with an empty (or shape-drifted) `favoritesList` must NOT
    self-heal-wipe an already-warm cache (F1 fix): `cache_entries` would be
    `[]`, and an unconditional `replace_site_cache_sync(s, site, [])` call is
    a full-replace that deletes every cached row for the site -- including
    ones the extension already pushed. The extension's own empty push stays
    the one authoritative way to clear the cache; a merely-empty SSR read
    must leave it alone."""
    with tasks_base.sync_session() as s:
        remote_collections_service.replace_site_cache_sync(
            s,
            ImportSite.MAKERWORLD,
            [
                remote_collections_service.CacheEntry(
                    list_id="2155987", title="Default Collection", count=7, is_default=True
                ),
            ],
        )
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld, "_favorites_client", lambda token: _mock_favorites_client(token)
    )
    monkeypatch.setattr(makerworld, "_web_client", lambda: _mock_collections_client([]))

    lists = MakerWorldImporter().list_user_lists()
    # Returned lists still include the cached collection, merged in as usual
    # (M10 escape hatch A) -- the empty SSR response just contributed nothing
    # new, it didn't erase what was already warm.
    assert [entry.list_id for entry in lists] == [str(fx.PROFILE_TERMINALFOO["uid"]), "2155987"]

    with tasks_base.sync_session() as s:
        cached = {
            row.list_id: row
            for row in remote_collections_service.get_site_cache(s, ImportSite.MAKERWORLD)
        }
    assert set(cached) == {"2155987"}  # untouched, not wiped


def test_list_user_lists_still_returns_aggregate_when_collections_route_fails(
    monkeypatch, _cache_db
):
    # The SSR collections.json route Cloudflare-challenges from a server IP
    # (intermittent, live-verified) -- must not take down the aggregate.
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld, "_favorites_client", lambda token: _mock_favorites_client(token)
    )
    monkeypatch.setattr(makerworld, "_web_client", _challenged_web_client)
    lists = MakerWorldImporter().list_user_lists()
    assert len(lists) == 1
    assert lists[0].list_id == str(fx.PROFILE_TERMINALFOO["uid"])
    assert lists[0].title == "All collected models"


def test_list_user_lists_merges_warm_cache_when_collections_route_fails(monkeypatch, _cache_db):
    """A warm cache (a past extension push, or a past successful SSR read)
    fills in the named collections a WALLED SSR attempt can't reach this
    time -- and a cached row equal to the profile uid must not duplicate the
    aggregate that's always emitted separately."""
    with tasks_base.sync_session() as s:
        remote_collections_service.replace_site_cache_sync(
            s,
            ImportSite.MAKERWORLD,
            [
                remote_collections_service.CacheEntry(
                    list_id="2155987", title="Default Collection", count=7, is_default=True
                ),
                remote_collections_service.CacheEntry(
                    list_id=str(fx.PROFILE_TERMINALFOO["uid"]), title="All collected models"
                ),
            ],
        )
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld, "_favorites_client", lambda token: _mock_favorites_client(token)
    )
    monkeypatch.setattr(makerworld, "_web_client", _challenged_web_client)

    lists = MakerWorldImporter().list_user_lists()
    assert [entry.list_id for entry in lists] == [str(fx.PROFILE_TERMINALFOO["uid"]), "2155987"]
    assert lists[1].title == "Default Collection" and lists[1].count == 7


def test_list_user_lists_ssr_entry_beats_stale_cache_title_for_same_id(monkeypatch, _cache_db):
    """A fresh SSR read wins over a stale cached title for the same id."""
    with tasks_base.sync_session() as s:
        stale = remote_collections_service.CacheEntry(
            list_id="2155987", title="Stale Title", count=1
        )
        remote_collections_service.replace_site_cache_sync(s, ImportSite.MAKERWORLD, [stale])
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld, "_favorites_client", lambda token: _mock_favorites_client(token)
    )
    monkeypatch.setattr(
        makerworld, "_web_client", lambda: _mock_collections_client(fx.FAVORITES_LIST)
    )

    lists = MakerWorldImporter().list_user_lists()
    named = {entry.list_id: entry for entry in lists[1:]}
    assert named["2155987"].title == "Default Collection"  # fresh SSR wins, not "Stale Title"


def test_list_list_items_maps_hits_to_search_results(monkeypatch):
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    list_id = str(fx.PROFILE_TERMINALFOO["uid"])
    monkeypatch.setattr(
        makerworld,
        "_favorites_client",
        lambda token: _mock_favorites_client(token, hits_by_list={list_id: fx.FAVORITE_DESIGNS}),
    )
    results = MakerWorldImporter().list_list_items(list_id)
    assert [r.external_id for r in results] == ["2188414", "2603954"]
    first = results[0]
    assert first.site is ImportSite.MAKERWORLD
    assert first.title == "ESP32-C6-Zigbee Gehäuse"
    assert first.url == "https://www.makerworld.com/en/models/2188414"
    assert first.author == "Jackstyle"
    assert first.thumbnail_url == fx.FAVORITE_DESIGNS[0]["cover"]


def test_list_list_items_falls_back_to_cache_when_live_returns_zero_hits_for_a_named_list(
    monkeypatch, _items_db
):
    """LIVE-VERIFIED 2026-07-11: the real endpoint serves ONLY the uid
    aggregate -- a genuinely non-empty named collection still comes back
    `{"hits": []}` from a server IP. `list_list_items` must fall back to
    whatever the extension pushed for this (site, list_id) via
    `remote_collection_items` rather than reporting the collection as empty.
    """
    list_id = "18925823"
    with tasks_base.sync_session() as s:
        remote_collections_service.replace_list_items_sync(
            s,
            ImportSite.MAKERWORLD,
            list_id,
            [
                remote_collections_service.ItemEntry(
                    external_id="111",
                    title="ESP32 case",
                    url="https://www.makerworld.com/en/models/111",
                    author="someone",
                    thumbnail_url="https://makerworld.bblmw.com/cover1.jpg",
                ),
                remote_collections_service.ItemEntry(
                    external_id="222",
                    title="ESP32 mount",
                    url="https://www.makerworld.com/en/models/222",
                ),
            ],
        )
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld,
        "_favorites_client",
        lambda token: _mock_favorites_client(token, hits_by_list={list_id: []}),
    )

    results = MakerWorldImporter().list_list_items(list_id)
    assert [r.external_id for r in results] == ["111", "222"]
    assert results[0].title == "ESP32 case" and results[0].author == "someone"
    assert results[0].thumbnail_url == "https://makerworld.bblmw.com/cover1.jpg"
    assert results[1].author is None and results[1].thumbnail_url is None


def test_list_list_items_cache_fallback_honors_paging(monkeypatch, _items_db):
    list_id = "18925823"
    entries = [
        remote_collections_service.ItemEntry(
            external_id=str(i), title=f"item {i}", url=f"https://www.makerworld.com/en/models/{i}"
        )
        for i in range(1, 26)  # 25 pushed items, _SEARCH_PAGE_SIZE is 20
    ]
    with tasks_base.sync_session() as s:
        remote_collections_service.replace_list_items_sync(
            s, ImportSite.MAKERWORLD, list_id, entries
        )
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld,
        "_favorites_client",
        lambda token: _mock_favorites_client(token, hits_by_list={list_id: []}),
    )

    page1 = MakerWorldImporter().list_list_items(list_id, page=1)
    page2 = MakerWorldImporter().list_list_items(list_id, page=2)
    assert [r.external_id for r in page1] == [str(i) for i in range(1, 21)]
    assert [r.external_id for r in page2] == [str(i) for i in range(21, 26)]


def test_list_list_items_live_hit_wins_over_cache(monkeypatch, _items_db):
    """A live (non-empty) result always wins over the cached fallback --
    important if MakerWorld ever fixes the server-side endpoint."""
    list_id = "18925823"
    with tasks_base.sync_session() as s:
        remote_collections_service.replace_list_items_sync(
            s,
            ImportSite.MAKERWORLD,
            list_id,
            [
                remote_collections_service.ItemEntry(
                    external_id="stale-cached-id",
                    title="Stale cached item",
                    url="https://www.makerworld.com/en/models/999999",
                )
            ],
        )
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld,
        "_favorites_client",
        lambda token: _mock_favorites_client(token, hits_by_list={list_id: fx.FAVORITE_DESIGNS}),
    )

    results = MakerWorldImporter().list_list_items(list_id)
    assert [r.external_id for r in results] == ["2188414", "2603954"]


def test_list_list_items_uid_never_falls_back_to_cache(monkeypatch, _items_db):
    """The aggregate list (list_id == uid) works live off just the uid
    (VERIFIED elsewhere in this module) -- an empty result for IT must be
    trusted as "genuinely no items", never masked by a cache fallback meant
    for named collections the live endpoint can't reach."""
    uid = str(fx.PROFILE_TERMINALFOO["uid"])
    with tasks_base.sync_session() as s:
        remote_collections_service.replace_list_items_sync(
            s,
            ImportSite.MAKERWORLD,
            uid,
            [
                remote_collections_service.ItemEntry(
                    external_id="should-not-appear",
                    title="x",
                    url="https://www.makerworld.com/en/models/1",
                )
            ],
        )
    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")
    monkeypatch.setattr(
        makerworld,
        "_favorites_client",
        lambda token: _mock_favorites_client(token, hits_by_list={uid: []}),
    )

    assert MakerWorldImporter().list_list_items(uid) == []


def test_resolve_download_hits_authed_endpoint_and_returns_presigned_url(monkeypatch):
    # NOTE: this authenticated download response shape is DOCUMENTED-NOT-
    # LIVE-CAPTURED (see MakerWorldImporter._fetch_authed_download_url's
    # docstring) -- the fixture body below is a best guess, not a recorded
    # real response, and must be reconciled against a real Bambu account at
    # acceptance.
    monkeypatch.setattr(makerworld, "_bambu_session", lambda: ("test-access-token", "global"))
    monkeypatch.setattr(makerworld, "_client", lambda: _mock_design_client(fx.DESIGN_3018898))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/iot-service/api/user/profile/12345"
        assert dict(request.url.params) == {"model_id": fx.DESIGN_3018898["modelId"]}
        assert request.headers.get("Authorization") == "Bearer test-access-token"
        return httpx.Response(200, json={"url": "https://makerworld.bblmw.com/signed/file.zip"})

    monkeypatch.setattr(
        makerworld,
        "_authed_client",
        lambda token, region="global": httpx.Client(
            base_url="https://api.bambulab.com/v1",
            headers={"Authorization": f"Bearer {token}"},
            transport=httpx.MockTransport(handler),
        ),
    )
    out = MakerWorldImporter().resolve_download(
        fx.DESIGN_ID, ImportFile(remote_id="12345", filename="Default-3391581.zip")
    )
    assert out.url == "https://makerworld.bblmw.com/signed/file.zip"
    assert out.filename == "Default-3391581.zip"
    # No Bearer forwarded to the presigned CDN URL itself (Thingiverse
    # posture: never leak the app/account token to a public/signed asset URL).
    assert out.headers == {}
