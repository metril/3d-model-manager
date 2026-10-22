"""``/ext/*`` browser-extension endpoints (M10 Workstream A): bearer-token
auth (``require_api_token``), the endpoints themselves, and -- most
importantly -- that the two auth planes (session cookie vs. bearer token)
don't leak into each other (SPEC: a leaked extension token must not unlock
the session-gated API, and a stolen session cookie can't be replayed here).
"""

from __future__ import annotations

import httpx
import pytest

from app.services import api_tokens
from tests import corpus

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


@pytest.fixture
async def ext_token(db_session, admin_user) -> str:
    token, _ = await api_tokens.mint(db_session, user_id=admin_user.id, label="Chrome extension")
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_ping_with_valid_bearer_is_200(client: httpx.AsyncClient, ext_token: str):
    r = await client.get("/api/ext/ping", headers=_bearer(ext_token))
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_ping_with_no_authorization_header_is_401(client: httpx.AsyncClient):
    r = await client.get("/api/ext/ping")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_ping_with_malformed_authorization_header_is_401(client: httpx.AsyncClient):
    r = await client.get("/api/ext/ping", headers={"Authorization": "not-a-bearer-token"})
    assert r.status_code == 401


async def test_ping_with_revoked_token_is_401(
    client: httpx.AsyncClient, db_session, ext_token: str
):
    row = (await api_tokens.list_tokens(db_session))[0]
    assert await api_tokens.revoke(db_session, row.id) is True

    r = await client.get("/api/ext/ping", headers=_bearer(ext_token))
    assert r.status_code == 401


async def test_ping_with_session_cookie_only_is_401(authenticated_client: httpx.AsyncClient):
    """Scope isolation: a session cookie (no bearer) must not authenticate
    against the /ext plane."""
    r = await authenticated_client.get("/api/ext/ping")
    assert r.status_code == 401


async def test_session_gated_route_with_bearer_only_is_401(
    client: httpx.AsyncClient, ext_token: str
):
    """Scope isolation, the other direction: a bearer token (no session
    cookie) must not authenticate against the session-gated API."""
    r = await client.get("/api/settings/import-tokens", headers=_bearer(ext_token))
    assert r.status_code == 401


async def test_create_import_creates_and_dedups(
    client: httpx.AsyncClient, ext_token: str, fake_import
):
    fake_import.files = {"cube.stl": corpus.box_stl()}
    first = await client.post(
        "/api/ext/imports", json={"url": "https://fake.test/thing/42"}, headers=_bearer(ext_token)
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["site"] == "thingiverse" and body["external_id"] == "42"

    second = await client.post(
        "/api/ext/imports", json={"url": "https://fake.test/thing/42"}, headers=_bearer(ext_token)
    )
    assert second.status_code == 200, second.text  # deduped: nothing new created
    assert second.json()["id"] == body["id"]


async def test_create_import_requires_bearer(client: httpx.AsyncClient):
    r = await client.post("/api/ext/imports", json={"url": "https://fake.test/thing/42"})
    assert r.status_code == 401


async def test_get_import_status_returns_narrow_shape(
    client: httpx.AsyncClient, ext_token: str, fake_import
):
    fake_import.files = {"cube.stl": corpus.box_stl()}
    created = await client.post(
        "/api/ext/imports", json={"url": "https://fake.test/thing/42"}, headers=_bearer(ext_token)
    )
    assert created.status_code == 201, created.text
    import_id = created.json()["id"]

    r = await client.get(f"/api/ext/imports/{import_id}", headers=_bearer(ext_token))
    assert r.status_code == 200, r.text
    # Exactly {id, state, error} -- no url/site/external_id/model_id/meta:
    # the ext plane must not become a data-exfiltration read surface.
    assert set(r.json()) == {"id", "state", "error"}
    assert r.json()["id"] == import_id
    assert r.json()["state"] in {"pending", "fetching", "downloading", "done", "failed"}


async def test_get_import_status_unknown_id_is_404(client: httpx.AsyncClient, ext_token: str):
    r = await client.get("/api/ext/imports/999999", headers=_bearer(ext_token))
    assert r.status_code == 404


async def test_get_import_status_requires_bearer(client: httpx.AsyncClient):
    r = await client.get("/api/ext/imports/1")
    assert r.status_code == 401


async def test_set_makerworld_credential_preserves_thingiverse_token(
    client: httpx.AsyncClient, db_session, ext_token: str
):
    from app.config import get_settings
    from app.services import import_tokens

    await import_tokens.set_import_tokens(
        db_session, get_settings(), thingiverse_token="tv-tok", makerworld_token=None
    )

    r = await client.post(
        "/api/ext/credentials/makerworld",
        json={"token": "mw-cookie-value"},
        headers=_bearer(ext_token),
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "mw-cookie-value" not in r.text  # never echoed back

    tokens = await import_tokens.get_import_tokens(db_session, get_settings())
    assert tokens.makerworld_token == "mw-cookie-value"
    assert tokens.thingiverse_token == "tv-tok"  # clobber guard: untouched


async def test_set_makerworld_credential_requires_bearer(client: httpx.AsyncClient):
    r = await client.post("/api/ext/credentials/makerworld", json={"token": "x"})
    assert r.status_code == 401


async def test_push_collections_creates_rows(client: httpx.AsyncClient, ext_token: str):
    r = await client.post(
        "/api/ext/collections",
        json={
            "site": "makerworld",
            "collections": [
                {"list_id": "1", "title": "Default Collection", "is_default": True, "count": 7},
                {"list_id": "2", "title": "ESP32", "slug": "esp32", "count": 9},
            ],
        },
        headers=_bearer(ext_token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 2}

    from app.models.enums import ImportSite
    from app.services import remote_collections
    from app.tasks import base as tasks_base

    with tasks_base.sync_session() as s:
        cache = remote_collections.get_site_cache(s, ImportSite.MAKERWORLD)
        cached = {row.list_id: row for row in cache}
    assert set(cached) == {"1", "2"}
    assert cached["1"].title == "Default Collection" and cached["1"].is_default is True
    assert cached["2"].slug == "esp32" and cached["2"].count == 9


async def test_push_collections_second_push_replaces_the_first(
    client: httpx.AsyncClient, ext_token: str
):
    from app.models.enums import ImportSite
    from app.services import remote_collections
    from app.tasks import base as tasks_base

    async def _push(collections):
        r = await client.post(
            "/api/ext/collections",
            json={"site": "makerworld", "collections": collections},
            headers=_bearer(ext_token),
        )
        assert r.status_code == 200, r.text
        return r.json()

    first = await _push(
        [
            {"list_id": "1", "title": "Default Collection"},
            {"list_id": "2", "title": "ESP32"},
        ]
    )
    assert first == {"ok": True, "count": 2}

    # Second push: "1" is renamed, "2" is dropped (unfollowed remotely), "3"
    # is new -- the push is authoritative for the whole site, not a merge.
    second = await _push(
        [
            {"list_id": "1", "title": "Default Collection (renamed)"},
            {"list_id": "3", "title": "New Collection"},
        ]
    )
    assert second == {"ok": True, "count": 2}

    with tasks_base.sync_session() as s:
        cache = remote_collections.get_site_cache(s, ImportSite.MAKERWORLD)
        cached = {row.list_id: row for row in cache}
    assert set(cached) == {"1", "3"}
    assert cached["1"].title == "Default Collection (renamed)"


async def test_push_collections_over_200_entries_is_422(client: httpx.AsyncClient, ext_token: str):
    collections = [{"list_id": str(i), "title": f"Collection {i}"} for i in range(201)]
    r = await client.post(
        "/api/ext/collections",
        json={"site": "makerworld", "collections": collections},
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_push_collections_bad_site_is_422(client: httpx.AsyncClient, ext_token: str):
    r = await client.post(
        "/api/ext/collections",
        json={"site": "not-a-real-site", "collections": []},
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_push_collections_requires_bearer(client: httpx.AsyncClient):
    r = await client.post("/api/ext/collections", json={"site": "makerworld", "collections": []})
    assert r.status_code == 401


async def test_push_collections_over_length_title_is_422(client: httpx.AsyncClient, ext_token: str):
    r = await client.post(
        "/api/ext/collections",
        json={
            "site": "makerworld",
            "collections": [{"list_id": "1", "title": "x" * 513}],
        },
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_push_collections_over_length_list_id_is_422(
    client: httpx.AsyncClient, ext_token: str
):
    r = await client.post(
        "/api/ext/collections",
        json={
            "site": "makerworld",
            "collections": [{"list_id": "1" * 129, "title": "Collection"}],
        },
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_push_collection_items_creates_rows(client: httpx.AsyncClient, ext_token: str):
    r = await client.post(
        "/api/ext/collections/18925823/items",
        json={
            "site": "makerworld",
            "items": [
                {
                    "external_id": "111",
                    "title": "ESP32 case",
                    "url": "https://makerworld.com/en/models/111-esp32-case",
                    "author": "someone",
                    "thumbnail_url": "https://makerworld.bblmw.com/cover1.jpg",
                },
                {
                    "external_id": "222",
                    "title": "ESP32 mount",
                    "url": "https://makerworld.com/en/models/222",
                },
            ],
        },
        headers=_bearer(ext_token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 2}

    from app.models.enums import ImportSite
    from app.services import remote_collections
    from app.tasks import base as tasks_base

    with tasks_base.sync_session() as s:
        rows = remote_collections.get_list_items(s, ImportSite.MAKERWORLD, "18925823")
    assert [row.external_id for row in rows] == ["111", "222"]
    assert rows[0].title == "ESP32 case" and rows[0].author == "someone"
    assert rows[0].thumbnail_url == "https://makerworld.bblmw.com/cover1.jpg"
    assert rows[1].author is None and rows[1].thumbnail_url is None


async def test_push_collection_items_second_push_replaces_the_first(
    client: httpx.AsyncClient, ext_token: str
):
    from app.models.enums import ImportSite
    from app.services import remote_collections
    from app.tasks import base as tasks_base

    async def _push(items):
        r = await client.post(
            "/api/ext/collections/18925823/items",
            json={"site": "makerworld", "items": items},
            headers=_bearer(ext_token),
        )
        assert r.status_code == 200, r.text
        return r.json()

    first = await _push(
        [
            {
                "external_id": "111",
                "title": "ESP32 case",
                "url": "https://makerworld.com/en/models/111",
            },
            {
                "external_id": "222",
                "title": "ESP32 mount",
                "url": "https://makerworld.com/en/models/222",
            },
        ]
    )
    assert first == {"ok": True, "count": 2}

    # Second push: "111" is renamed, "222" dropped, "333" is new -- authoritative
    # replace for this (site, list_id), same posture as the collection-list push.
    second = await _push(
        [
            {
                "external_id": "111",
                "title": "ESP32 case (renamed)",
                "url": "https://makerworld.com/en/models/111",
            },
            {
                "external_id": "333",
                "title": "New item",
                "url": "https://makerworld.com/en/models/333",
            },
        ]
    )
    assert second == {"ok": True, "count": 2}

    with tasks_base.sync_session() as s:
        rows = {
            row.external_id: row
            for row in remote_collections.get_list_items(s, ImportSite.MAKERWORLD, "18925823")
        }
    assert set(rows) == {"111", "333"}
    assert rows["111"].title == "ESP32 case (renamed)"


async def test_push_collection_items_scoped_to_list_id(client: httpx.AsyncClient, ext_token: str):
    """Pushing items for one list_id must not touch a different list_id's
    rows -- the replace-set is scoped to (site, list_id), not the whole site."""
    from app.models.enums import ImportSite
    from app.services import remote_collections
    from app.tasks import base as tasks_base

    for list_id, external_id in (("18925823", "111"), ("2155987", "222")):
        r = await client.post(
            f"/api/ext/collections/{list_id}/items",
            json={
                "site": "makerworld",
                "items": [
                    {
                        "external_id": external_id,
                        "title": f"item {external_id}",
                        "url": f"https://makerworld.com/en/models/{external_id}",
                    }
                ],
            },
            headers=_bearer(ext_token),
        )
        assert r.status_code == 200, r.text

    with tasks_base.sync_session() as s:
        a = remote_collections.get_list_items(s, ImportSite.MAKERWORLD, "18925823")
        b = remote_collections.get_list_items(s, ImportSite.MAKERWORLD, "2155987")
    assert [row.external_id for row in a] == ["111"]
    assert [row.external_id for row in b] == ["222"]


async def test_push_collection_items_over_500_is_422(client: httpx.AsyncClient, ext_token: str):
    items = [
        {
            "external_id": str(i),
            "title": f"item {i}",
            "url": f"https://makerworld.com/en/models/{i}",
        }
        for i in range(501)
    ]
    r = await client.post(
        "/api/ext/collections/18925823/items",
        json={"site": "makerworld", "items": items},
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_push_collection_items_bad_url_is_422_naming_the_first_bad_url(
    client: httpx.AsyncClient, ext_token: str
):
    r = await client.post(
        "/api/ext/collections/18925823/items",
        json={
            "site": "makerworld",
            "items": [
                {
                    "external_id": "111",
                    "title": "OK",
                    "url": "https://makerworld.com/en/models/111",
                },
                {
                    "external_id": "999",
                    "title": "Bad",
                    "url": "https://example.com/not-a-makerworld-model",
                },
            ],
        },
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422
    assert "https://example.com/not-a-makerworld-model" in r.text

    # Nothing was written -- a bad url rejects the whole request, not a
    # partial push.
    from app.models.enums import ImportSite
    from app.services import remote_collections
    from app.tasks import base as tasks_base

    with tasks_base.sync_session() as s:
        rows = remote_collections.get_list_items(s, ImportSite.MAKERWORLD, "18925823")
    assert rows == []


async def test_push_collection_items_bad_site_is_422(client: httpx.AsyncClient, ext_token: str):
    r = await client.post(
        "/api/ext/collections/18925823/items",
        json={"site": "not-a-real-site", "items": []},
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_push_collection_items_requires_bearer(client: httpx.AsyncClient):
    r = await client.post(
        "/api/ext/collections/18925823/items", json={"site": "makerworld", "items": []}
    )
    assert r.status_code == 401


async def test_push_collection_items_over_length_url_is_422(
    client: httpx.AsyncClient, ext_token: str
):
    over_length_url = "https://makerworld.com/en/models/111-" + ("x" * 1000)
    r = await client.post(
        "/api/ext/collections/18925823/items",
        json={
            "site": "makerworld",
            "items": [{"external_id": "111", "title": "ESP32 case", "url": over_length_url}],
        },
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_push_collection_items_over_length_external_id_is_422(
    client: httpx.AsyncClient, ext_token: str
):
    r = await client.post(
        "/api/ext/collections/18925823/items",
        json={
            "site": "makerworld",
            "items": [
                {
                    "external_id": "1" * 129,
                    "title": "ESP32 case",
                    "url": "https://makerworld.com/en/models/111",
                }
            ],
        },
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_member_owned_token_cannot_set_makerworld_credential(
    client: httpx.AsyncClient, db_session, member
):
    token, _ = await api_tokens.mint(db_session, user_id=member.id, label="member ext")
    r = await client.post(
        "/api/ext/credentials/makerworld", json={"token": "x"}, headers=_bearer(token)
    )
    assert r.status_code == 403
