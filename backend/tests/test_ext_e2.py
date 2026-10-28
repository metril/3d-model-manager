"""E2 browser-extension additions: ``/ext/me``, single-entry collection upsert,
import save options (collection + tags), local collection / tag lists and the
v2 ``printables`` lookup."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.models.collections import FollowedCollection, RemoteCollectionCache
from app.models.enums import BlobFormat, CollectionSyncMode, ImportSite
from app.models.library import Model, Revision, Tag
from app.services import api_tokens
from tests import corpus

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


@pytest.fixture
async def ext_token(db_session, admin_user) -> str:
    token, _ = await api_tokens.mint(db_session, user_id=admin_user.id, label="Chrome extension")
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _followed(**kw) -> FollowedCollection:
    return FollowedCollection(
        site=ImportSite.THINGIVERSE,
        list_id=kw.pop("list_id", "x"),
        kind="collection",
        title=kw.pop("title", "Favs"),
        mode=CollectionSyncMode.REVIEW,
    )


async def test_me_returns_username_capped_perms_and_version(
    client: httpx.AsyncClient, ext_token: str, admin_user
):
    r = await client.get("/api/ext/me", headers=_bearer(ext_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == admin_user.username
    assert "library:write" in body["perms"]
    assert set(body["perms"]) <= {"library:write", "printers:print"}
    assert isinstance(body["app_version"], str) and body["app_version"]


async def test_me_requires_bearer(client: httpx.AsyncClient):
    assert (await client.get("/api/ext/me")).status_code == 401


async def test_put_collection_upserts_one_without_deleting_others(
    client: httpx.AsyncClient, ext_token: str, db_session
):
    h = _bearer(ext_token)
    await client.post(
        "/api/ext/collections",
        json={
            "site": "makerworld",
            "collections": [{"list_id": "a", "title": "A"}, {"list_id": "b", "title": "B"}],
        },
        headers=h,
    )
    r = await client.put(
        "/api/ext/collections/b",
        json={"site": "makerworld", "title": "B2", "slug": "b2", "count": 3, "is_default": False},
        headers=h,
    )
    assert r.status_code == 200 and r.json() == {"ok": True, "count": 1}
    r = await client.put(
        "/api/ext/collections/c", json={"site": "makerworld", "title": "C"}, headers=h
    )
    assert r.status_code == 200
    rows = (await db_session.execute(select(RemoteCollectionCache))).scalars().all()
    by_id = {x.list_id: x for x in rows}
    assert set(by_id) == {"a", "b", "c"}
    assert by_id["b"].title == "B2" and by_id["b"].count == 3


async def test_put_collection_requires_bearer(client: httpx.AsyncClient):
    r = await client.put("/api/ext/collections/a", json={"site": "makerworld", "title": "A"})
    assert r.status_code == 401


async def test_local_collections_and_tags_lists(
    client: httpx.AsyncClient, ext_token: str, db_session
):
    coll = _followed()
    db_session.add_all([coll, Tag(name="benchy")])
    await db_session.commit()
    h = _bearer(ext_token)
    r = await client.get("/api/ext/local-collections", headers=h)
    assert r.status_code == 200 and r.json() == [{"id": coll.id, "name": "Favs"}]
    r = await client.get("/api/ext/tags", headers=h)
    assert r.status_code == 200 and r.json() == [{"name": "benchy"}]
    for path in ("/api/ext/local-collections", "/api/ext/tags"):
        assert (await client.get(path)).status_code == 401


async def test_create_import_applies_collection_and_tags_on_completion(
    client: httpx.AsyncClient, ext_token: str, db_session, fake_import
):
    fake_import.files = {"cube.stl": corpus.box_stl()}
    coll = _followed()
    db_session.add(coll)
    await db_session.commit()
    coll_id = coll.id
    h = _bearer(ext_token)
    r = await client.post(
        "/api/ext/imports",
        json={
            "url": "https://fake.test/thing/42",
            "collection_id": coll_id,
            "tags": ["Mine", "mine"],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    status = (await client.get(f"/api/ext/imports/{r.json()['id']}", headers=h)).json()
    assert status["state"] == "done" and status["model_id"] is not None
    db_session.expire_all()
    model = (
        await db_session.execute(select(Model).where(Model.id == status["model_id"]))
    ).scalar_one()
    assert model.source_collection_id == coll_id
    await db_session.refresh(model, ["tags"])
    assert [t.name.lower() for t in model.tags].count("mine") == 1


async def test_create_import_unknown_collection_is_422(client: httpx.AsyncClient, ext_token: str):
    r = await client.post(
        "/api/ext/imports",
        json={"url": "https://fake.test/thing/42", "collection_id": 99999},
        headers=_bearer(ext_token),
    )
    assert r.status_code == 422


async def test_v2_model_printables_lists_sliced_files_only(
    authenticated_client, client, db_session, seed_file
):
    model = Model(slug="pr", name="pr")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, name="r", dir_name="r1")
    db_session.add(rev)
    await db_session.flush()
    model.current_revision_id = rev.id
    await db_session.commit()
    await seed_file(model, rev, "a.stl", b"stl-bytes")
    sliced = await seed_file(
        model, rev, "plates/b.gcode.3mf", b"g3mf", blob_format=BlobFormat.GCODE_3MF
    )

    async def mint(scopes):
        r = await authenticated_client.post(
            "/api/settings/api-keys", json={"label": "k", "scopes": scopes}
        )
        assert r.status_code == 201, r.text
        return {"X-API-Key": r.json()["key"]}

    read = await mint(["read"])
    r = await client.get(f"/api/ext/v2/models/{model.id}/printables", headers=read)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"file_id": sliced.id, "name": "b.gcode.3mf", "kind": "sliced", "plate": None}
    ]
    assert (
        await client.get("/api/ext/v2/models/999999/printables", headers=read)
    ).status_code == 404
    queue = await mint(["queue"])
    r = await client.get(f"/api/ext/v2/models/{model.id}/printables", headers=queue)
    assert r.status_code == 403


async def test_create_import_dedups_in_flight_import(
    client: httpx.AsyncClient, ext_token: str, db_session, fake_import
):
    from app.models.enums import ImportState
    from app.models.system import Import

    pending = Import(
        url="https://fake.test/thing/77",
        site=ImportSite.THINGIVERSE,
        external_id="77",
        state=ImportState.DOWNLOADING,
    )
    db_session.add(pending)
    await db_session.commit()
    r = await client.post(
        "/api/ext/imports", json={"url": "https://fake.test/thing/77"}, headers=_bearer(ext_token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == pending.id
    rows = (await db_session.execute(select(Import).where(Import.external_id == "77"))).all()
    assert len(rows) == 1


async def test_me_can_set_credentials_follows_owner_grant(
    client: httpx.AsyncClient, ext_token: str, db_session, member
):
    r = await client.get("/api/ext/me", headers=_bearer(ext_token))
    assert r.json()["can_set_credentials"] is True  # admin owner holds settings:write
    mtoken, _ = await api_tokens.mint(db_session, user_id=member.id, label="member ext")
    r = await client.get("/api/ext/me", headers=_bearer(mtoken))
    assert r.json()["can_set_credentials"] is False


async def test_put_collection_omitted_fields_preserve_existing(
    client: httpx.AsyncClient, ext_token: str, db_session
):
    h = _bearer(ext_token)
    await client.put(
        "/api/ext/collections/d",
        json={"site": "makerworld", "title": "D", "count": 9, "is_default": True},
        headers=h,
    )
    r = await client.put(
        "/api/ext/collections/d", json={"site": "makerworld", "title": "D2"}, headers=h
    )
    assert r.status_code == 200
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(RemoteCollectionCache).where(RemoteCollectionCache.list_id == "d")
        )
    ).scalar_one()
    assert row.title == "D2" and row.count == 9 and row.is_default is True
    # explicit null count clears it; fresh insert defaults hold
    await client.put(
        "/api/ext/collections/d",
        json={"site": "makerworld", "title": "D2", "count": None},
        headers=h,
    )
    await client.put("/api/ext/collections/e", json={"site": "makerworld", "title": "E"}, headers=h)
    db_session.expire_all()
    rows = {
        x.list_id: x for x in (await db_session.execute(select(RemoteCollectionCache))).scalars()
    }
    assert rows["d"].count is None and rows["d"].is_default is True
    assert rows["e"].count is None and rows["e"].is_default is False
