import json

import httpx
import pytest

from app.importers import printables
from app.importers.base import RemoteList
from app.importers.printables import PrintablesImporter
from app.models.enums import ImportSite
from app.services.printables_auth import PrintablesAuthError
from tests.cassettes import printables_fixtures as fx


def _mock_client(print_body, link_body=None, search_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        if "searchPrints2" in payload:
            variables = json.loads(payload)["variables"]
            assert variables["query"] == fx.SEARCH_QUERY
            assert variables["limit"] == 20 and variables["offset"] == 0
            return httpx.Response(200, json=search_body or fx.SEARCH_PRINTS_BENCHY)
        if "getDownloadLink" in payload:
            # Guard the live getDownloadLink signature (it drifted once, and
            # the mock happily returns a link regardless of what we send): the
            # request MUST carry the current args -- printId, a model_detail
            # source, and files:[{fileType:"stl", ids:[...]}] with the
            # LOWERCASE enum. A future drift breaks this assert, not silently
            # ships a broken importer.
            variables = json.loads(payload)["variables"]
            assert variables["printId"] == fx.MODEL_ID
            assert variables["source"] == "model_detail"
            assert variables["files"] == [{"fileType": "stl", "ids": ["90001"]}]
            return httpx.Response(200, json=link_body or fx.DOWNLOAD_LINK_90001)
        return httpx.Response(200, json=print_body)

    return httpx.Client(
        base_url="https://api.printables.com/graphql/", transport=httpx.MockTransport(handler)
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.printables.com/model/3161-benchy", "3161"),
        ("https://www.printables.com/en/model/3161", "3161"),
        ("https://thingiverse.com/thing:1", None),
    ],
)
def test_canonicalize(url, expected):
    assert PrintablesImporter().canonicalize(url) == expected


def test_fetch_metadata_free_model(monkeypatch):
    monkeypatch.setattr(printables, "_client", lambda: _mock_client(fx.PRINT_3161))
    meta = PrintablesImporter().fetch_metadata(fx.MODEL_ID)
    assert meta.site is ImportSite.PRINTABLES and meta.title == "Benchy"
    assert meta.author == "printables_user" and meta.license == "CC-BY-4.0"
    assert set(meta.tags) == {"boat", "calibration"}
    assert meta.cover_url.endswith("media/prints/3161/cover.png")
    assert meta.reject_reason is None


def test_fetch_metadata_image_urls_cover_first_deduped_and_tolerant(monkeypatch):
    monkeypatch.setattr(printables, "_client", lambda: _mock_client(fx.PRINT_3161))
    meta = PrintablesImporter().fetch_metadata(fx.MODEL_ID)
    assert meta.image_urls == [
        "https://media.printables.com/media/prints/3161/cover.png",
        "https://media.printables.com/media/prints/3161/images/side.jpg",
    ]
    assert meta.image_urls[0] == meta.cover_url


def test_fetch_metadata_image_urls_falls_back_to_cover_alone_without_images_field(monkeypatch):
    print_body = {
        "data": {
            "print": {
                **fx.PRINT_3161["data"]["print"],
                "images": [],
            }
        }
    }
    monkeypatch.setattr(printables, "_client", lambda: _mock_client(print_body))
    meta = PrintablesImporter().fetch_metadata(fx.MODEL_ID)
    assert meta.image_urls == ["https://media.printables.com/media/prints/3161/cover.png"]


def test_premium_model_is_rejected_with_clear_message(monkeypatch):
    monkeypatch.setattr(printables, "_client", lambda: _mock_client(fx.PRINT_3161_PREMIUM))
    meta = PrintablesImporter().fetch_metadata(fx.MODEL_ID)
    assert meta.reject_reason and "paid" in meta.reject_reason.lower()


def test_list_files_from_stls(monkeypatch):
    monkeypatch.setattr(printables, "_client", lambda: _mock_client(fx.PRINT_3161))
    files = PrintablesImporter().list_files(fx.MODEL_ID)
    assert [f.filename for f in files] == ["3DBenchy.stl", "3DBenchy_hollow.stl"]
    assert files[0].remote_id == "90001"


def test_resolve_download_returns_cdn_link(monkeypatch):
    monkeypatch.setattr(printables, "_client", lambda: _mock_client(fx.PRINT_3161))
    from app.importers.base import ImportFile

    out = PrintablesImporter().resolve_download(
        fx.MODEL_ID, ImportFile(remote_id="90001", filename="3DBenchy.stl")
    )
    assert out.url.startswith("https://files.printables.com/media/dl/3161/3DBenchy.stl")


def test_search_maps_hits_to_search_results(monkeypatch):
    monkeypatch.setattr(printables, "_client", lambda: _mock_client(fx.PRINT_3161))
    results = PrintablesImporter().search(fx.SEARCH_QUERY)
    assert [r.title for r in results] == ["3D BENCHY", "All Terrain Assault Benchy"]
    first = results[0]
    assert first.site is ImportSite.PRINTABLES and first.external_id == "3161"
    assert first.url == "https://www.printables.com/model/3161"
    assert first.author == "Prusa Research"
    assert first.thumbnail_url.endswith("media/prints/3161/images/20206_70fde6a0/benchy.jpg")


def test_search_empty_query_returns_empty_list_without_a_request(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("search() must not make a request for an empty query")

    monkeypatch.setattr(
        printables,
        "_client",
        lambda: httpx.Client(
            base_url="https://api.printables.com/graphql/", transport=httpx.MockTransport(handler)
        ),
    )
    assert PrintablesImporter().search("") == []


def _mock_authed_client(handler, expected_token="test-token"):
    def build(token: str) -> httpx.Client:
        assert token == expected_token
        return httpx.Client(
            base_url="https://api.printables.com/graphql/", transport=httpx.MockTransport(handler)
        )

    return build


def _stub_session_and_identity(monkeypatch, *, token="test-token", identity=("5092991", "someone")):
    monkeypatch.setattr(printables, "_printables_session", lambda: token)
    monkeypatch.setattr(printables, "fetch_identity", lambda access_token: identity)


def test_list_user_lists_maps_collections_and_appends_likes(monkeypatch):
    _stub_session_and_identity(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        assert "userCollections" in payload["query"]
        assert payload["variables"] == {"userId": "5092991"}
        return httpx.Response(200, json=fx.USER_COLLECTIONS)

    monkeypatch.setattr(printables, "_authed_client", _mock_authed_client(handler))
    lists = PrintablesImporter().list_user_lists()
    assert [item.kind for item in lists] == ["collection", "likes"]
    collection = lists[0]
    assert collection.site is ImportSite.PRINTABLES
    assert collection.list_id == "3585865"
    assert collection.title == "Stuff"
    assert collection.count == 1
    assert lists[1] == RemoteList(
        site=ImportSite.PRINTABLES, list_id="likes", kind="likes", title="Liked models", count=None
    )


def test_list_user_lists_not_connected_returns_empty_list(monkeypatch):
    def raise_auth_error():
        raise PrintablesAuthError("no Printables account is connected")

    monkeypatch.setattr(printables, "_printables_session", raise_auth_error)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make a request when not connected")

    monkeypatch.setattr(printables, "_authed_client", _mock_authed_client(handler))
    assert PrintablesImporter().list_user_lists() == []


def test_list_list_items_for_a_collection_maps_search_results(monkeypatch):
    _stub_session_and_identity(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        assert "moreCollectionModels" in payload["query"]
        variables = payload["variables"]
        assert variables["collectionId"] == "3585865"
        assert variables["ordering"] == "added_to_collection"
        return httpx.Response(200, json=fx.COLLECTION_MODELS)

    monkeypatch.setattr(printables, "_authed_client", _mock_authed_client(handler))
    results = PrintablesImporter().list_list_items("3585865")
    assert len(results) == 1
    result = results[0]
    assert result.site is ImportSite.PRINTABLES
    assert result.external_id == "605259"
    assert result.title == "Dual Color Poker Chips With Numbers"
    assert result.url == (
        "https://www.printables.com/model/605259-dual-color-poker-chips-with-numbers"
    )
    assert result.author == "agepbiz"
    assert result.thumbnail_url == (
        "https://media.printables.com/media/prints/605259/images/abc123/poker_chips.jpg"
    )


def test_list_list_items_likes_routes_to_liked_models_query(monkeypatch):
    _stub_session_and_identity(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        assert "moreLikedPrints2" in payload["query"]
        variables = payload["variables"]
        assert variables["userId"] == "5092991"
        assert variables["printType"] == "all"
        return httpx.Response(200, json=fx.LIKED_MODELS)

    monkeypatch.setattr(printables, "_authed_client", _mock_authed_client(handler))
    results = PrintablesImporter().list_list_items("likes")
    assert len(results) == 1
    assert results[0].external_id == "605259"


def test_list_list_items_row_without_slug_falls_back_to_bare_url(monkeypatch):
    _stub_session_and_identity(monkeypatch)
    body = {
        "data": {
            "models": {
                "cursor": "",
                "items": [
                    {
                        "id": "9",
                        "model": {
                            "id": "9",
                            "name": "No Slug Model",
                            "slug": None,
                            "user": None,
                            "image": None,
                        },
                    }
                ],
            }
        }
    }
    monkeypatch.setattr(
        printables, "_authed_client", _mock_authed_client(lambda r: httpx.Response(200, json=body))
    )
    results = PrintablesImporter().list_list_items("3585865")
    assert len(results) == 1
    assert results[0].url == "https://www.printables.com/model/9"
    assert results[0].author is None
    assert results[0].thumbnail_url is None


def test_list_list_items_row_without_model_is_skipped(monkeypatch):
    _stub_session_and_identity(monkeypatch)
    body = {
        "data": {
            "models": {
                "cursor": "",
                "items": [
                    {"id": "9", "model": None},
                    {"id": "10", "model": {"id": None, "name": "Broken"}},
                ],
            }
        }
    }
    monkeypatch.setattr(
        printables, "_authed_client", _mock_authed_client(lambda r: httpx.Response(200, json=body))
    )
    assert PrintablesImporter().list_list_items("3585865") == []


def test_list_list_items_not_connected_returns_empty_list(monkeypatch):
    def raise_auth_error():
        raise PrintablesAuthError("no Printables account is connected")

    monkeypatch.setattr(printables, "_printables_session", raise_auth_error)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make a request when not connected")

    monkeypatch.setattr(printables, "_authed_client", _mock_authed_client(handler))
    assert PrintablesImporter().list_list_items("3585865") == []
    assert PrintablesImporter().list_list_items("likes") == []


def _paginated_item(i: int) -> dict:
    return {
        "id": str(i),
        "model": {
            "id": str(i),
            "name": f"Model {i}",
            "slug": f"model-{i}",
            "user": {"publicUsername": "someone"},
            "image": None,
        },
    }


def test_list_list_items_page_2_requests_limit_40_and_returns_the_upper_window(monkeypatch):
    _stub_session_and_identity(monkeypatch)
    all_items = [_paginated_item(i) for i in range(40)]

    def handler(request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.read().decode())["variables"]
        assert variables["limit"] == 40
        assert variables["cursor"] is None
        return httpx.Response(200, json={"data": {"models": {"cursor": "", "items": all_items}}})

    monkeypatch.setattr(printables, "_authed_client", _mock_authed_client(handler))
    results = PrintablesImporter().list_list_items("3585865", page=2)
    assert [r.external_id for r in results] == [str(i) for i in range(20, 40)]


def test_list_list_items_follows_cursor_when_server_caps_limit(monkeypatch):
    _stub_session_and_identity(monkeypatch)
    first_batch = [_paginated_item(i) for i in range(20)]
    second_batch = [_paginated_item(i) for i in range(20, 40)]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.read().decode())["variables"]
        calls.append(variables)
        if variables["cursor"] is None:
            # Server caps `limit` at 20 despite the 40 asked for, but says
            # there's more via a non-empty cursor.
            assert variables["limit"] == 40
            return httpx.Response(
                200,
                json={"data": {"models": {"cursor": "cursor-1", "items": first_batch}}},
            )
        assert variables["cursor"] == "cursor-1"
        assert variables["limit"] == 20
        return httpx.Response(200, json={"data": {"models": {"cursor": "", "items": second_batch}}})

    monkeypatch.setattr(printables, "_authed_client", _mock_authed_client(handler))
    results = PrintablesImporter().list_list_items("3585865", page=2)
    assert len(calls) == 2
    assert [r.external_id for r in results] == [str(i) for i in range(20, 40)]


@pytest.mark.live_importer
def test_live_printables_metadata():
    """Deferred/manual live smoke (SPEC "one live smoke"), excluded from the
    default gate. Run with `-m live_importer`; hits the real GraphQL API.

    Printables is anonymous (no token to naturally gate this on, unlike the
    Thingiverse live smoke), and `-m 'not e2e'` in pyproject's `addopts`
    does not deselect `live_importer` until Task 7's addopts change lands --
    so an explicit opt-in env var keeps this test from making a real network
    call every time the default `uv run pytest` gate runs.
    """
    import os

    if not os.environ.get("LIVE_PRINTABLES"):
        pytest.skip("set LIVE_PRINTABLES=1 to run the live smoke")
    meta = PrintablesImporter().fetch_metadata(fx.MODEL_ID)
    assert meta.title and meta.external_id == fx.MODEL_ID


def test_paged_items_follows_the_memoised_cursor_instead_of_refetching(monkeypatch):
    from app.importers import printables

    size = printables._SEARCH_PAGE_SIZE
    printables._cursors.clear()
    calls: list[dict] = []

    def fake_fetch(client, query, variables):
        calls.append(variables)
        start = int(variables["cursor"] or 0)
        n = variables["limit"]
        return [{"id": i} for i in range(start, start + n)], str(start + n)

    monkeypatch.setattr(printables, "_fetch_page", fake_fetch)
    p1 = printables._paged_items(None, "q", {"id": "7"}, 1)
    p2 = printables._paged_items(None, "q", {"id": "7"}, 2)
    p3 = printables._paged_items(None, "q", {"id": "7"}, 3)
    assert [i["id"] for i in p2] == list(range(size, 2 * size))
    assert [i["id"] for i in p3] == list(range(2 * size, 3 * size))
    assert len(p1) == size
    # Each page costs one page-sized request, not page * size.
    assert [c["limit"] for c in calls] == [size, size, size]
    printables._cursors.clear()
