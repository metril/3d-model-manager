"""Shared tag vocabulary: CRUD, merge, per-entity sets, any/all filters,
file tags across revisions, cascades, and the migration."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import asyncpg
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.models import File, Model, Revision
from app.models.collections import FollowedCollection
from app.models.enums import CollectionSyncMode, ImportSite
from app.models.library import Tag
from app.models.tag_links import model_file_tags
from tests.migration_helpers import _run_alembic
from tests.test_archives_api import _archive, _printer

pytestmark = pytest.mark.usefixtures("library_root")


def _u(prefix: str = "t") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _tag(c: httpx.AsyncClient, **kw) -> dict:
    r = await c.post("/api/tags", json={"name": _u(), **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _model(c: httpx.AsyncClient) -> dict:
    r = await c.post("/api/models", json={"name": _u("m")})
    assert r.status_code == 201, r.text
    return r.json()


async def _project(c: httpx.AsyncClient) -> dict:
    r = await c.post("/api/projects", json={"name": _u("p")})
    assert r.status_code == 201, r.text
    return r.json()


async def test_crud_conflicts_and_validation(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    t = await _tag(c, color="#AABBCC", icon="star", description="d")
    assert t["color"] == "#aabbcc" and t["icon"] == "star" and t["description"] == "d"
    assert (await c.post("/api/tags", json={"name": t["name"].upper()})).status_code == 409
    assert (await c.post("/api/tags", json={"name": _u(), "color": "red"})).status_code == 422
    other = await _tag(c)
    r = await c.patch(f"/api/tags/{other['id']}", json={"name": t["name"].upper()})
    assert r.status_code == 409
    r = await c.patch(f"/api/tags/{t['id']}", json={"icon": None, "description": "e"})
    assert r.json()["icon"] is None and r.json()["description"] == "e"
    assert r.json()["color"] == "#aabbcc"  # untouched
    assert (await c.patch("/api/tags/999999", json={"icon": "x"})).status_code == 404
    assert (await c.delete(f"/api/tags/{t['id']}")).status_code == 204
    assert (await c.delete(f"/api/tags/{t['id']}")).status_code == 404


async def test_legacy_add_is_case_insensitive_get_or_create(
    authenticated_client: httpx.AsyncClient,
) -> None:
    c = authenticated_client
    m = await _model(c)
    name = _u("Mixed")
    a = await c.post(f"/api/models/{m['id']}/tags", json={"name": name})
    b = await c.post(f"/api/models/{m['id']}/tags", json={"name": name.lower()})
    assert a.json()["id"] == b.json()["id"]


async def test_counts_and_per_entity_sets(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    t1, t2 = await _tag(c), await _tag(c)
    m, p = await _model(c), await _project(c)
    r = await c.put(f"/api/models/{m['id']}/tags", json={"tag_ids": [t1["id"], t2["id"]]})
    assert r.status_code == 200 and len(r.json()) == 2
    detail = (await c.get(f"/api/models/{m['slug']}")).json()
    assert {x["id"] for x in detail["tag_refs"]} == {t1["id"], t2["id"]}
    assert sorted(detail["tags"]) == sorted([t1["name"], t2["name"]])
    r = await c.put(f"/api/models/{m['id']}/tags", json={"tag_ids": [t1["id"]]})
    assert [x["id"] for x in r.json()] == [t1["id"]]
    bad = await c.put(f"/api/models/{m['id']}/tags", json={"tag_ids": [999999]})
    assert bad.status_code == 404
    await c.put(f"/api/projects/{p['id']}/tags", json={"tag_ids": [t1["id"]]})
    assert (await c.put("/api/projects/999999/tags", json={"tag_ids": []})).status_code == 404

    listed = {t["id"]: t for t in (await c.get("/api/tags", params={"with_counts": True})).json()}
    assert listed[t1["id"]]["counts"]["models"] == 1
    assert listed[t1["id"]]["counts"]["projects"] == 1
    assert listed[t1["id"]]["counts"]["total"] == 2
    assert listed[t2["id"]]["counts"]["total"] == 0
    plain = (await c.get("/api/tags")).json()
    assert all(t["counts"] is None for t in plain)


async def test_merge_collapses_conflicts(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    src, dst = await _tag(c), await _tag(c)
    both, only_src = await _model(c), await _model(c)
    await c.put(f"/api/models/{both['id']}/tags", json={"tag_ids": [src["id"], dst["id"]]})
    await c.put(f"/api/models/{only_src['id']}/tags", json={"tag_ids": [src["id"]]})
    p = await _project(c)
    await c.put(f"/api/projects/{p['id']}/tags", json={"tag_ids": [src["id"]]})

    same = await c.post(f"/api/tags/{src['id']}/merge", json={"into_id": src["id"]})
    assert same.status_code == 400
    missing = await c.post(f"/api/tags/{src['id']}/merge", json={"into_id": 999999})
    assert missing.status_code == 404
    r = await c.post(f"/api/tags/{src['id']}/merge", json={"into_id": dst["id"]})
    assert r.status_code == 200 and r.json()["id"] == dst["id"]

    ids = {t["id"] for t in (await c.get("/api/tags")).json()}
    assert src["id"] not in ids
    for m in (both, only_src):
        refs = (await c.get(f"/api/models/{m['slug']}")).json()["tag_refs"]
        assert [x["id"] for x in refs] == [dst["id"]]
    proj = (await c.get(f"/api/projects/{p['id']}")).json()
    assert [x["id"] for x in proj["tag_refs"]] == [dst["id"]]


async def test_model_any_all_filters(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    a, b = await _tag(c), await _tag(c)
    m_ab, m_a, m_none = await _model(c), await _model(c), await _model(c)
    await c.put(f"/api/models/{m_ab['id']}/tags", json={"tag_ids": [a["id"], b["id"]]})
    await c.put(f"/api/models/{m_a['id']}/tags", json={"tag_ids": [a["id"]]})

    async def ids(**params) -> set[int]:
        r = await c.get("/api/models", params=params)
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["items"]}

    assert await ids(tags=[a["id"], b["id"]], tag_mode="any") == {m_ab["id"], m_a["id"]}
    assert await ids(tags=[a["id"], b["id"]], tag_mode="all") == {m_ab["id"]}
    assert await ids(tags=[a["id"]]) == {m_ab["id"], m_a["id"]}
    assert await ids(tag=a["name"].upper()) == {m_ab["id"], m_a["id"]}  # alias
    assert m_none["id"] not in await ids(tags=[a["id"]])


async def test_file_tags_filters_and_survive_revision(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    c = authenticated_client
    a, b = await _tag(c), await _tag(c)
    m = await _model(c)
    model = await db_session.get(Model, m["id"])
    rev1 = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, rev1, "a.stl", b"aaa")
    await seed_file(model, rev1, "b.stl", b"bbb")

    url = f"/api/models/{m['id']}/files/tags"
    r = await c.put(url, json={"rel_path": "a.stl", "tag_ids": [a["id"], b["id"]]})
    assert r.status_code == 200 and len(r.json()) == 2
    await c.put(url, json={"rel_path": "b.stl", "tag_ids": [a["id"]]})
    bad = await c.put(url, json={"rel_path": "nope.stl", "tag_ids": []})
    assert bad.status_code == 404

    files = (await c.get(f"/api/models/{m['id']}/files")).json()
    by_path = {f["rel_path"]: f for f in files}
    assert len(by_path["a.stl"]["tag_refs"]) == 2 and len(by_path["b.stl"]["tag_refs"]) == 1
    both = [a["id"], b["id"]]
    sub = (
        await c.get(f"/api/models/{m['id']}/files", params={"tags": both, "tag_mode": "all"})
    ).json()
    assert [f["rel_path"] for f in sub] == ["a.stl"]
    sub = (await c.get(f"/api/models/{m['id']}/files", params={"tags": both})).json()
    assert len(sub) == 2

    # gallery file_tags filter (a single file must carry all for mode=all)
    only_b = await _tag(c)
    r = await c.get("/api/models", params={"file_tags": both, "file_tag_mode": "all"})
    assert m["id"] in {i["id"] for i in r.json()["items"]}
    # tag_mode no longer drives the file filter: model tag_mode=all + file any still matches
    r = await c.get(
        "/api/models", params={"file_tags": both, "tag_mode": "all", "file_tag_mode": "any"}
    )
    assert m["id"] in {i["id"] for i in r.json()["items"]}
    r = await c.get("/api/models", params={"file_tags": [only_b["id"]]})
    assert m["id"] not in {i["id"] for i in r.json()["items"]}

    # new revision: tags persist on the same rel_path
    rev = await c.post(f"/api/models/{m['id']}/revisions", json={})
    assert rev.status_code == 201
    new_files = {f["rel_path"]: f for f in rev.json()["files"]}
    assert len(new_files["a.stl"]["tag_refs"]) == 2
    files = (await c.get(f"/api/models/{m['id']}/files")).json()
    assert {f["rel_path"]: len(f["tag_refs"]) for f in files} == {"a.stl": 2, "b.stl": 1}


async def test_project_attachment_archive_collection_tags(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = authenticated_client
    a, b = await _tag(c), await _tag(c)
    both = [a["id"], b["id"]]

    p1, p2 = await _project(c), await _project(c)
    await c.put(f"/api/projects/{p1['id']}/tags", json={"tag_ids": both})
    await c.put(f"/api/projects/{p2['id']}/tags", json={"tag_ids": [a["id"]]})
    r = await c.get("/api/projects", params={"tags": both, "tag_mode": "all"})
    assert [x["id"] for x in r.json()] == [p1["id"]]
    r = await c.get("/api/projects", params={"tags": both})
    assert {x["id"] for x in r.json()} == {p1["id"], p2["id"]}

    up = await c.post(
        f"/api/projects/{p1['id']}/attachments",
        params={"filename": "n.txt"},
        content=b"hi",
        headers={"content-type": "text/plain"},
    )
    aid = up.json()["id"]
    r = await c.put(f"/api/projects/{p1['id']}/attachments/{aid}/tags", json={"tag_ids": [a["id"]]})
    assert r.status_code == 200
    wrong = await c.put(f"/api/projects/{p2['id']}/attachments/{aid}/tags", json={"tag_ids": []})
    assert wrong.status_code == 404
    att = (await c.get(f"/api/projects/{p1['id']}")).json()["attachments"][0]
    assert [x["id"] for x in att["tag_refs"]] == [a["id"]]

    printer = await _printer(db_session, _u("pr"))
    arc1 = await _archive(db_session, printer, n=1)
    arc2 = await _archive(db_session, printer, n=2)
    await c.put(f"/api/archives/{arc1.id}/tags", json={"tag_ids": both})
    await c.put(f"/api/archives/{arc2.id}/tags", json={"tag_ids": [a["id"]]})
    r = await c.get("/api/archives", params={"tags": both, "tag_mode": "all"})
    assert [x["id"] for x in r.json()] == [arc1.id]
    r = await c.get("/api/archives", params={"tags": [a["id"]]})
    assert {x["id"] for x in r.json()} == {arc1.id, arc2.id}
    assert len((await c.get(f"/api/archives/{arc1.id}")).json()["tag_refs"]) == 2

    def _col(title: str) -> FollowedCollection:
        return FollowedCollection(
            site=ImportSite.MAKERWORLD,
            list_id=_u("l"),
            kind="collection",
            title=title,
            mode=CollectionSyncMode.REVIEW,
        )

    col, col2 = _col("C"), _col("C2")
    db_session.add_all([col, col2])
    await db_session.commit()
    await c.put(f"/api/collections/{col.id}/tags", json={"tag_ids": both})
    await c.put(f"/api/collections/{col2.id}/tags", json={"tag_ids": [a["id"]]})
    r = await c.get("/api/collections", params={"tags": both, "tag_mode": "all"})
    assert [x["id"] for x in r.json()] == [col.id]
    r = await c.get("/api/collections", params={"tags": [a["id"]]})
    assert {x["id"] for x in r.json()} == {col.id, col2.id}
    assert len(next(x for x in r.json() if x["id"] == col.id)["tag_refs"]) == 2


async def test_delete_tag_and_entity_cascade(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = authenticated_client
    t = await _tag(c)
    m, p = await _model(c), await _project(c)
    await c.put(f"/api/models/{m['id']}/tags", json={"tag_ids": [t["id"]]})
    await c.put(f"/api/projects/{p['id']}/tags", json={"tag_ids": [t["id"]]})
    # deleting the tag removes its usages everywhere
    assert (await c.delete(f"/api/tags/{t['id']}")).status_code == 204
    assert (await c.get(f"/api/models/{m['slug']}")).json()["tag_refs"] == []
    assert (await c.get(f"/api/projects/{p['id']}")).json()["tag_refs"] == []

    # deleting the entity removes its join rows but keeps the tag
    t2 = await _tag(c)
    await c.put(f"/api/projects/{p['id']}/tags", json={"tag_ids": [t2["id"]]})
    await c.delete(f"/api/projects/{p['id']}")
    counts = {x["id"]: x for x in (await c.get("/api/tags", params={"with_counts": True})).json()}
    assert counts[t2["id"]]["counts"]["total"] == 0
    assert await db_session.scalar(select(Tag.id).where(Tag.id == t2["id"])) == t2["id"]
    rows = (
        await db_session.execute(
            select(model_file_tags).where(model_file_tags.c.tag_id == t2["id"])
        )
    ).all()
    assert rows == []


_PREV = "e3b7d1f5a9c2"
_NEW = "a9d4c7e1b5f3"


async def test_migration_up_down_maps_colors(scratch_db) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tags (name, color) VALUES ('Dup', 'red'), ('dup', NULL), ('x', 'teal')"
        )
        mid = await conn.fetchval(
            "INSERT INTO models (name, slug) VALUES ('m', 'm-slug') RETURNING id"
        )
        dup_id = await conn.fetchval("SELECT id FROM tags WHERE name = 'dup'")
        await conn.execute("INSERT INTO model_tags (model_id, tag_id) VALUES ($1, $2)", mid, dup_id)
        pid = await conn.fetchval(
            "INSERT INTO projects (name, tags) "
            "VALUES ('p', ARRAY['Legacy','legacy','x']) RETURNING id"
        )
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, _NEW)
    conn = await asyncpg.connect(dsn)
    try:
        rows = {r["name"]: r["color"] for r in await conn.fetch("SELECT name, color FROM tags")}
        assert set(rows) == {"Dup", "x", "Legacy"}  # case-dupe merged into lowest id
        assert rows["Dup"] == "#ef4444" and rows["x"] == "#14b8a6" and rows["Legacy"] is None
        assert (
            await conn.fetchval("SELECT count(*) FROM project_tags WHERE project_id=$1", pid) == 2
        )
        assert await conn.fetchval("SELECT count(*) FROM model_tags") == 1
        keep = await conn.fetchval("SELECT id FROM tags WHERE name = 'Dup'")
        assert await conn.fetchval("SELECT tag_id FROM model_tags") == keep
        for t in (
            "model_file_tags",
            "project_tags",
            "project_attachment_tags",
            "print_archive_tags",
            "followed_collection_tags",
        ):
            assert await conn.fetchval("SELECT to_regclass($1)", t) is not None
        await conn.execute("UPDATE tags SET icon = 'star', description = 'd' WHERE name = 'x'")
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute("INSERT INTO tags (name) VALUES ('DUP')")
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _PREV)
    conn = await asyncpg.connect(dsn)
    try:
        rows = {r["name"]: r["color"] for r in await conn.fetch("SELECT name, color FROM tags")}
        assert rows == {"Dup": "red", "x": "teal", "Legacy": None}
        assert await conn.fetchval("select to_regclass('project_tags')") is None
        cols = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='tags' AND column_name IN ('icon','description')"
        )
        assert cols == 0
    finally:
        await conn.close()


async def test_project_tags_require_owner(
    authenticated_client: httpx.AsyncClient, member_client: httpx.AsyncClient
) -> None:
    t = await _tag(authenticated_client)
    p = await _project(authenticated_client)
    r = await member_client.put(f"/api/projects/{p['id']}/tags", json={"tag_ids": [t["id"]]})
    assert r.status_code in (403, 404)
    r = await member_client.put(
        f"/api/projects/{p['id']}/attachments/1/tags", json={"tag_ids": [t["id"]]}
    )
    assert r.status_code in (403, 404)


async def test_bulk_add_case_variant_tags(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    m = await _model(c)
    r = await c.post("/api/models/bulk", json={"ids": [m["id"]], "add_tags": ["PLA", "pla"]})
    assert r.status_code == 200, r.text


async def test_bulk_delete_merge_update_and_usage(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    a, b, dst, other = [await _tag(c) for _ in range(4)]
    m1, m2, p = await _model(c), await _model(c), await _project(c)
    await c.put(f"/api/models/{m1['id']}/tags", json={"tag_ids": [a["id"], dst["id"]]})
    await c.put(f"/api/models/{m2['id']}/tags", json={"tag_ids": [b["id"]]})
    await c.put(f"/api/projects/{p['id']}/tags", json={"tag_ids": [a["id"]]})

    u = (await c.get(f"/api/tags/{a['id']}/usage")).json()
    assert u["counts"]["models"] == 1 and u["counts"]["projects"] == 1
    assert [x["id"] for x in u["models"]] == [m1["id"]] and u["models"][0]["slug"] == m1["slug"]
    assert [x["id"] for x in u["projects"]] == [p["id"]]
    assert (await c.get("/api/tags/999999/usage")).status_code == 404

    bad = {"ids": [a["id"], dst["id"]], "into_id": dst["id"]}
    assert (await c.post("/api/tags/bulk-merge", json=bad)).status_code == 422
    nt = {"ids": [a["id"]], "into_id": 999999}
    assert (await c.post("/api/tags/bulk-merge", json=nt)).status_code == 404
    ok = {"ids": [a["id"], b["id"], 999998], "into_id": dst["id"]}
    r = await c.post("/api/tags/bulk-merge", json=ok)
    assert r.status_code == 200, r.text
    assert sorted(x["id"] for x in r.json()["results"]) == sorted([a["id"], b["id"]])
    assert r.json()["not_found"] == [999998]
    tags = {t["id"]: t for t in (await c.get("/api/tags?with_counts=true")).json()}
    assert a["id"] not in tags and b["id"] not in tags
    assert tags[dst["id"]]["counts"]["models"] == 2 and tags[dst["id"]]["counts"]["projects"] == 1

    body = {"ids": [dst["id"], other["id"]], "color": "#ABCDEF"}
    r = await c.post("/api/tags/bulk-update", json=body)
    assert r.status_code == 200 and len(r.json()["results"]) == 2
    tags = {t["id"]: t for t in (await c.get("/api/tags")).json()}
    assert tags[dst["id"]]["color"] == tags[other["id"]]["color"] == "#abcdef"
    assert (await c.post("/api/tags/bulk-update", json={"ids": [other["id"]]})).status_code == 422

    r = await c.post("/api/tags/bulk-delete", json={"ids": [dst["id"], other["id"], 999997]})
    assert r.status_code == 200 and r.json()["not_found"] == [999997]
    assert len(r.json()["results"]) == 2
    assert (await c.get(f"/api/models/{m1['slug']}")).json()["tag_refs"] == []
    assert (await c.post("/api/tags/bulk-delete", json={"ids": []})).status_code == 422


async def test_trashed_models_excluded_from_counts_and_usage(
    authenticated_client: httpx.AsyncClient,
) -> None:
    c = authenticated_client
    t, m = await _tag(c), await _model(c)
    await c.put(f"/api/models/{m['id']}/tags", json={"tag_ids": [t["id"]]})
    assert (await c.get(f"/api/tags/{t['id']}/usage")).json()["counts"]["models"] == 1
    assert (await c.delete(f"/api/models/{m['slug']}")).status_code == 204
    u = (await c.get(f"/api/tags/{t['id']}/usage")).json()
    assert u["counts"]["models"] == 0 and u["counts"]["total"] == 0 and u["models"] == []
    tags = {x["id"]: x for x in (await c.get("/api/tags?with_counts=true")).json()}
    assert tags[t["id"]]["counts"]["models"] == 0
