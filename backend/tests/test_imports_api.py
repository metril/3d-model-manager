import httpx
import pytest

from app.models.enums import ImportSite, ImportState
from app.models.system import Import
from app.services import imports as imports_service
from tests import corpus


async def _seed_import(
    db_session, *, state: ImportState, error: str | None = None, external_id: str = "42"
) -> Import:
    imp = Import(
        url=f"https://fake.test/thing/{external_id}",
        site=ImportSite.THINGIVERSE,
        external_id=external_id,
        state=state,
        error=error,
    )
    db_session.add(imp)
    await db_session.commit()
    await db_session.refresh(imp)
    return imp


@pytest.mark.asyncio
async def test_makerworld_import_is_rejected_pending_bambu_auth(
    authenticated_client, library_root, data_dir, monkeypatch
):
    """MakerWorld is no longer a deferred site (Workstream B task B1) -- a
    MakerWorld URL now builds the real importer and runs the normal import
    flow: fetch_metadata succeeds (this model isn't paid), but list_files
    raises the "needs a Bambu account" ImportRejected (downloads are B2)
    -- a clean FAILED row, never a crash, and no orphan model."""
    from app.importers import makerworld

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/design-service/design/999"
        return httpx.Response(
            200,
            json={
                "id": 999,
                "title": "MW Model",
                "summary": "a makerworld model",
                "designCreator": {"name": "mwuser"},
                "license": "Standard Digital File License",
                "coverUrl": "https://makerworld.bblmw.com/cover.jpg",
                "tags": ["gadget"],
                "isExclusive": False,
                "paidSetting": {"isPaid": False, "crowdfunding": 0},
            },
        )

    monkeypatch.setattr(
        makerworld,
        "_client",
        lambda: httpx.Client(
            base_url="https://makerworld.com/api/v1", transport=httpx.MockTransport(handler)
        ),
    )
    r = await authenticated_client.post(
        "/api/imports", json={"url": "https://makerworld.com/en/models/999"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["site"] == "makerworld" and body["external_id"] == "999"
    assert body["state"] == "failed" and body["model_id"] is None
    assert "Bambu" in body["error"]


@pytest.mark.asyncio
async def test_unsupported_url_is_rejected(authenticated_client, library_root, data_dir):
    r = await authenticated_client.post("/api/imports", json={"url": "https://example.com/x"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_full_import_flow_creates_model(
    authenticated_client, library_root, data_dir, fake_import
):
    fake_import.files = {"cube.stl": corpus.box_stl()}
    r = await authenticated_client.post("/api/imports", json={"url": "https://fake.test/thing/42"})
    assert r.status_code == 201, r.text
    body = r.json()
    # eager task ran inline -> already terminal
    assert body["state"] == "done" and body["model_id"] is not None
    assert body["site"] == "thingiverse" and body["external_id"] == "42"

    poll = await authenticated_client.get(f"/api/imports/{body['id']}")
    assert poll.json()["state"] == "done"

    gallery = await authenticated_client.get("/api/models")
    names = [m["name"] for m in gallery.json()["items"]]
    assert "Fake Thing" in names


@pytest.mark.asyncio
async def test_rejected_import_leaves_no_model(
    authenticated_client, library_root, data_dir, fake_import
):
    fake_import.reject_reason = "This is a paid model and can't be imported"
    fake_import.files = {"cube.stl": corpus.box_stl()}
    r = await authenticated_client.post("/api/imports", json={"url": "https://fake.test/thing/42"})
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "failed" and body["model_id"] is None
    assert "paid" in body["error"]
    # atomicity: no model created
    gallery = await authenticated_client.get("/api/models")
    assert gallery.json()["items"] == []


@pytest.mark.asyncio
async def test_reimporting_the_same_model_returns_the_existing_import(
    authenticated_client, library_root, data_dir, fake_import
):
    """M8 H cross-import dedup: a second import of the same (site, external_id)
    hands back the existing import (200, nothing created) instead of minting a
    duplicate Model -- without this a periodic collection sync would duplicate
    every followed model on every run."""
    fake_import.files = {"cube.stl": corpus.box_stl()}
    first = await authenticated_client.post(
        "/api/imports", json={"url": "https://fake.test/thing/42"}
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["state"] == "done" and first_body["model_id"] is not None

    second = await authenticated_client.post(
        "/api/imports", json={"url": "https://fake.test/thing/42"}
    )
    assert second.status_code == 200, second.text  # 200: nothing was created
    assert second.json()["id"] == first_body["id"]
    assert second.json()["model_id"] == first_body["model_id"]

    gallery = await authenticated_client.get("/api/models")
    assert len(gallery.json()["items"]) == 1  # still exactly one model


@pytest.mark.asyncio
async def test_a_failed_import_does_not_block_retrying_the_same_model(
    authenticated_client, library_root, data_dir, fake_import
):
    """Only a DONE import that still points at a live Model blocks a re-import;
    a failed (or model-deleted) one must stay retryable."""
    fake_import.reject_reason = "This is a paid model and can't be imported"
    fake_import.files = {"cube.stl": corpus.box_stl()}
    first = await authenticated_client.post(
        "/api/imports", json={"url": "https://fake.test/thing/42"}
    )
    assert first.status_code == 201 and first.json()["state"] == "failed"

    fake_import.reject_reason = None
    second = await authenticated_client.post(
        "/api/imports", json={"url": "https://fake.test/thing/42"}
    )
    assert second.status_code == 201, second.text  # a NEW import row was created
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["state"] == "done"


@pytest.mark.asyncio
async def test_search_imports_dispatches_to_the_sites_importer(
    authenticated_client, library_root, data_dir, fake_import
):
    fake_import.title = "Searchable Fake"
    fake_import.external_id = "77"
    fake_import.author = "fakeuser"
    fake_import.cover_url = "https://fake.test/cover.png"
    r = await authenticated_client.get(
        "/api/imports/search", params={"site": "thingiverse", "q": "fake"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["results"] == [
        {
            "site": "thingiverse",
            "external_id": "77",
            "title": "Searchable Fake",
            "url": "https://fake.test/thing/77",
            "author": "fakeuser",
            "thumbnail_url": "https://fake.test/cover.png",
        }
    ]
    assert body["per_site"] == [
        {"site": "thingiverse", "count": 1, "has_more": False, "status": "ok", "detail": None}
    ]


@pytest.mark.asyncio
async def test_search_imports_empty_query_returns_empty_list(
    authenticated_client, library_root, data_dir, fake_import
):
    r = await authenticated_client.get(
        "/api/imports/search", params={"site": "thingiverse", "q": "   "}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == [] and body["per_site"] == []


@pytest.mark.asyncio
async def test_search_imports_rejects_unknown_site(authenticated_client, library_root, data_dir):
    r = await authenticated_client.get(
        "/api/imports/search", params={"site": "not-a-real-site", "q": "x"}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_imports_422_when_site_has_no_registered_importer(
    authenticated_client, library_root, data_dir, monkeypatch
):
    import app.api.imports as imports_api

    monkeypatch.setattr(imports_api, "get_importer", lambda site: None)
    r = await authenticated_client.get(
        "/api/imports/search", params={"site": "printables", "q": "benchy"}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_lists_endpoints_fan_out_and_return_items(
    authenticated_client, library_root, data_dir, monkeypatch
):
    """M8 H seam: `GET /imports/lists` merges each site's collections/likes and
    `GET /imports/lists/{site}/{list_id}/items` returns that list's models. Real
    importers return [] until their authenticated session is wired, so drive it
    with a stub that actually has lists."""
    from app.importers.base import RemoteList, SearchResult
    from app.models.enums import ImportSite

    class Stub:
        site = ImportSite.PRINTABLES

        def list_user_lists(self) -> list[RemoteList]:
            return [
                RemoteList(
                    site=ImportSite.PRINTABLES,
                    list_id="7",
                    kind="collection",
                    title="Desk stuff",
                    count=2,
                )
            ]

        def list_list_items(self, list_id: str, page: int = 1) -> list[SearchResult]:
            assert list_id == "7"
            return [
                SearchResult(
                    site=ImportSite.PRINTABLES,
                    external_id="1",
                    title="Cable clip",
                    url="https://www.printables.com/model/1",
                )
            ]

    monkeypatch.setattr(
        "app.importers.registry.IMPORTER_REGISTRY", {ImportSite.PRINTABLES: Stub()}, raising=True
    )

    lists = await authenticated_client.get("/api/imports/lists")
    assert lists.status_code == 200, lists.text
    assert lists.json() == [
        {
            "site": "printables",
            "list_id": "7",
            "kind": "collection",
            "title": "Desk stuff",
            "count": 2,
        }
    ]

    items = await authenticated_client.get("/api/imports/lists/printables/7/items")
    assert items.status_code == 200, items.text
    assert [i["external_id"] for i in items.json()] == ["1"]


@pytest.mark.asyncio
async def test_lists_endpoint_is_empty_when_no_site_has_an_authenticated_session(
    authenticated_client, library_root, data_dir
):
    """The real importers all return [] until their site auth lands -- the
    endpoint must degrade to an empty list, never an error."""
    r = await authenticated_client.get("/api/imports/lists")
    assert r.status_code == 200 and r.json() == []


@pytest.mark.asyncio
async def test_list_items_endpoint_serves_extension_pushed_membership_for_a_named_collection(
    authenticated_client, library_root, data_dir, monkeypatch
):
    """End-to-end (M10 Workstream A task 3): `GET /imports/lists/makerworld/
    {list_id}/items` serves the extension-pushed membership when MakerWorld's
    live items endpoint comes back empty for a named collection id -- the
    live-verified behaviour this branch works around (see
    `app.models.collections.RemoteCollectionItem`'s docstring). Exercises the
    real `MakerWorldImporter.list_list_items` through the real HTTP route,
    not a stub importer."""
    from app.importers import makerworld
    from app.models.enums import ImportSite
    from app.services import remote_collections
    from app.tasks import base as tasks_base

    list_id = "18925823"
    with tasks_base.sync_session() as s:
        remote_collections.replace_list_items_sync(
            s,
            ImportSite.MAKERWORLD,
            list_id,
            [
                remote_collections.ItemEntry(
                    external_id="111",
                    title="ESP32 case",
                    url="https://www.makerworld.com/en/models/111",
                    author="someone",
                    thumbnail_url="https://makerworld.bblmw.com/cover1.jpg",
                )
            ],
        )

    monkeypatch.setattr(makerworld, "_makerworld_web_token", lambda: "test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/user-service/my/profile":
            return httpx.Response(200, json={"uid": 3054026541, "name": "Terminalfoo"})
        if path == f"/api/v1/design-service/favorites/{list_id}/designs":
            # Live-verified shape a real named-collection id actually
            # returns: total 0, no hits, even though the collection has
            # items (that's exactly why the cache fallback exists).
            return httpx.Response(200, json={"hits": [], "total": 0})
        return httpx.Response(404, text="unexpected path")

    monkeypatch.setattr(
        makerworld,
        "_favorites_client",
        lambda token: httpx.Client(
            base_url="https://makerworld.com/api/v1", transport=httpx.MockTransport(handler)
        ),
    )

    r = await authenticated_client.get(f"/api/imports/lists/makerworld/{list_id}/items")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [item["external_id"] for item in body] == ["111"]
    assert body[0]["title"] == "ESP32 case"
    assert body[0]["author"] == "someone"
    assert body[0]["thumbnail_url"] == "https://makerworld.bblmw.com/cover1.jpg"
    assert body[0]["url"] == "https://www.makerworld.com/en/models/111"


@pytest.mark.asyncio
async def test_search_imports_federates_across_sites_and_isolates_errors(
    authenticated_client, library_root, data_dir, monkeypatch
):
    """No ``site`` fans out to every registered importer concurrently: results
    merge, and one upstream raising becomes that site's ``error`` status rather
    than a 500 for the whole search."""
    from app.importers.base import SearchResult
    from app.importers.registry import IMPORTER_REGISTRY
    from app.models.enums import ImportSite

    class OkImporter:
        site = ImportSite.PRINTABLES

        def search(self, query: str, page: int = 1) -> list[SearchResult]:
            return [
                SearchResult(
                    site=ImportSite.PRINTABLES,
                    external_id="1",
                    title="P1",
                    url="https://www.printables.com/model/1",
                )
            ]

    class BoomImporter:
        site = ImportSite.MAKERWORLD

        def search(self, query: str, page: int = 1) -> list[SearchResult]:
            raise RuntimeError("upstream down")

    monkeypatch.setattr(
        "app.importers.registry.IMPORTER_REGISTRY",
        {ImportSite.PRINTABLES: OkImporter(), ImportSite.MAKERWORLD: BoomImporter()},
        raising=True,
    )
    assert IMPORTER_REGISTRY  # sanity: patched dict is non-empty

    r = await authenticated_client.get("/api/imports/search", params={"q": "benchy"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [hit["external_id"] for hit in body["results"]] == ["1"]
    statuses = {row["site"]: row for row in body["per_site"]}
    assert statuses["printables"]["status"] == "ok" and statuses["printables"]["count"] == 1
    assert statuses["makerworld"]["status"] == "error"
    assert "upstream down" in statuses["makerworld"]["detail"]


# ---------------------------------------------------------------------------
# import-health T2: POST /imports/{import_id}/retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_failed_import_resets_state_clears_error_and_dispatches(
    authenticated_client, library_root, data_dir, db_session, monkeypatch
):
    """The recovery path once whatever failed the import (e.g. T1's dead-
    Bambu-session error) has been fixed: back to `pending`, `error` cleared,
    re-dispatched under the SAME `import-{id}` task_id `start_import` uses.
    `apply_async` is stubbed (not called through) so the row's `pending`
    reset is observable in the response, rather than being immediately
    overwritten by an inline eager-mode run."""
    imp = await _seed_import(
        db_session,
        state=ImportState.FAILED,
        error="Bambu sign-in expired -- reconnect your Bambu account in Settings.",
    )

    calls: list[tuple[list[int], str]] = []
    monkeypatch.setattr(
        imports_service.import_from_url,
        "apply_async",
        lambda *, args, task_id: calls.append((args, task_id)),
    )

    r = await authenticated_client.post(f"/api/imports/{imp.id}/retry")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == imp.id
    assert body["state"] == "pending"
    assert body["error"] is None
    assert calls == [([imp.id], f"import-{imp.id}")]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [ImportState.PENDING, ImportState.DONE])
async def test_retry_non_failed_import_is_409_and_does_not_enqueue(
    authenticated_client, library_root, data_dir, db_session, monkeypatch, state
):
    imp = await _seed_import(db_session, state=state)

    calls: list[tuple[list[int], str]] = []
    monkeypatch.setattr(
        imports_service.import_from_url,
        "apply_async",
        lambda *, args, task_id: calls.append((args, task_id)),
    )

    r = await authenticated_client.post(f"/api/imports/{imp.id}/retry")
    assert r.status_code == 409
    assert calls == []


@pytest.mark.asyncio
async def test_retry_unknown_import_is_404(authenticated_client, library_root, data_dir):
    r = await authenticated_client.post("/api/imports/999999/retry")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_retry_race_only_the_winner_enqueues(
    authenticated_client, library_root, data_dir, db_session, monkeypatch
):
    """M2 (import-health post-review fix): the eligibility check and the
    state flip are now one atomic conditional UPDATE
    (``WHERE id=:id AND state='failed'``) rather than a SELECT followed by a
    separate UPDATE, so two near-simultaneous retries of the SAME failed
    import can't both pass the guard and double-enqueue the same task_id.
    Calling retry twice in a row proves this: the first call's UPDATE flips
    the row out of `failed`, so the second call's UPDATE matches zero rows
    and 409s instead of also dispatching."""
    imp = await _seed_import(db_session, state=ImportState.FAILED, error="boom")

    calls: list[tuple[list[int], str]] = []
    monkeypatch.setattr(
        imports_service.import_from_url,
        "apply_async",
        lambda *, args, task_id: calls.append((args, task_id)),
    )

    first = await authenticated_client.post(f"/api/imports/{imp.id}/retry")
    second = await authenticated_client.post(f"/api/imports/{imp.id}/retry")

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert calls == [([imp.id], f"import-{imp.id}")]
