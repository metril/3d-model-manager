"""Studio sessions API + exchange ingest/sweeper (launcher mocked with respx)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import redis as redis_lib
import respx
from pydantic import SecretStr
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import File, Model, Revision
from app.services import server_config as sc
from app.services import studio_sessions as ss
from app.tasks.studio_exchange import scan_studio_exchange

LAUNCHER = "http://launcher.test:3010"
pytestmark = pytest.mark.usefixtures("library_root", "data_dir", "redis_url")


@pytest.fixture
def exchange(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "exchange"
    root.mkdir()
    monkeypatch.setenv("STUDIO_EXCHANGE_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def rds(redis_url: str) -> Iterator[redis_lib.Redis]:
    c = redis_lib.Redis.from_url(redis_url)
    yield c
    c.close()


async def _model(db, seed_file, slug: str, rel_path: str = "part.stl"):
    model = Model(slug=slug, name=slug)
    db.add(model)
    await db.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db.add(rev)
    await db.commit()
    await db.refresh(rev)
    f = await seed_file(model, rev, rel_path, b"solid x\nendsolid x\n")
    return model, rev, f


async def _enable() -> None:
    async with get_sessionmaker()() as db:
        cfg = await sc.get_server_config(db)
        await sc.set_server_config(
            db,
            cfg.model_copy(
                update={
                    "studio_enabled": True,
                    "studio_launcher_url": LAUNCHER,
                    "studio_launcher_token": SecretStr("tok"),
                }
            ),
        )


def _plant(rds, token: str, model: Model, rev: Revision, file: File) -> None:
    rec = {
        "model_id": model.id,
        "model_slug": model.slug,
        "revision_id": rev.id,
        "file_id": file.id,
        "file_name": file.rel_path,
        "user_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
    }
    rds.set(ss.session_key(token), json.dumps(rec), ex=3600)


def _drop(path: Path, data: bytes = b"returned", age: float = 3600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    old = time.time() - age
    os.utime(path, (old, old))
    return path


@respx.mock
async def test_create_session(authenticated_client, db_session, seed_file, exchange, rds) -> None:
    model, rev, f = await _model(db_session, seed_file, "st-create")
    await _enable()
    route = respx.post(f"{LAUNCHER}/open").respond(202, json={"pid": 1})
    r = await authenticated_client.post(f"/api/models/{model.slug}/files/{f.id}/studio")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["iframe_path"] == "/studio/" and body["replaced_session"] is False
    assert body["file"] == {"id": f.id, "name": "part.stl"}
    token = body["token"]
    assert (exchange / token / "in" / "part.stl").read_bytes() == b"solid x\nendsolid x\n"
    assert route.calls.last.request.headers["X-Launcher-Token"] == "tok"
    assert json.loads(route.calls.last.request.content) == {
        "path": f"/exchange/{token}/in/part.stl"
    }
    assert rds.get(ss.ACTIVE_KEY).decode() == token
    assert json.loads(rds.get(ss.session_key(token)))["file_id"] == f.id
    assert 0 < rds.ttl(ss.session_key(token)) <= ss.SESSION_TTL_S

    r2 = await authenticated_client.post(f"/api/models/{model.slug}/files/{f.id}/studio")
    assert r2.status_code == 201 and r2.json()["replaced_session"] is True
    assert rds.get(ss.ACTIVE_KEY).decode() == r2.json()["token"]


async def test_create_rejects(authenticated_client, db_session, seed_file, exchange) -> None:
    model, _, f = await _model(db_session, seed_file, "st-rej")
    url = f"/api/models/{model.slug}/files/{f.id}/studio"
    r = await authenticated_client.post(url)  # disabled by default
    assert r.status_code == 409 and r.json()["detail"]["code"] == "studio_disabled"
    assert (
        await authenticated_client.post(f"/api/models/{model.slug}/files/999999/studio")
    ).status_code == 404
    _, _, g = await _model(db_session, seed_file, "st-gc", "a.gcode")
    await _enable()
    assert (
        await authenticated_client.post(f"/api/models/st-gc/files/{g.id}/studio")
    ).status_code == 400


@respx.mock
async def test_launcher_down_leaves_no_state(
    authenticated_client, db_session, seed_file, exchange, rds
) -> None:
    model, _, f = await _model(db_session, seed_file, "st-down")
    await _enable()
    respx.post(f"{LAUNCHER}/open").mock(side_effect=httpx.ConnectError("nope"))
    r = await authenticated_client.post(f"/api/models/{model.slug}/files/{f.id}/studio")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "launcher_unreachable"
    assert rds.get(ss.ACTIVE_KEY) is None
    assert rds.keys("tdmm:studio:session:*") == []
    assert list(exchange.iterdir()) == []


@respx.mock
async def test_get_and_delete_session(
    authenticated_client, db_session, seed_file, exchange, rds
) -> None:
    assert (await authenticated_client.get("/api/studio/session")).status_code == 204
    model, rev, f = await _model(db_session, seed_file, "st-get")
    await _enable()
    respx.post(f"{LAUNCHER}/open").respond(202, json={})
    respx.get(f"{LAUNCHER}/health").respond(200, json={"ok": True, "studio_running": True})
    stop = respx.post(f"{LAUNCHER}/stop").respond(200, json={"ok": True})
    token = (
        await authenticated_client.post(f"/api/models/{model.slug}/files/{f.id}/studio")
    ).json()["token"]

    r = await authenticated_client.get("/api/studio/session")
    assert r.status_code == 200
    b = r.json()
    assert b["token"] == token and b["model_slug"] == model.slug and b["model_id"] == model.id
    assert b["revision_id"] == rev.id and b["file"] == {"id": f.id, "name": "part.stl"}
    assert b["studio_running"] is True and b["created_at"] < b["expires_at"]

    assert (await authenticated_client.delete("/api/studio/session")).status_code == 204
    assert stop.called and stop.calls.last.request.headers["X-Launcher-Token"] == "tok"
    assert rds.get(ss.ACTIVE_KEY) is None and rds.get(ss.session_key(token)) is None
    assert (exchange / token / "in" / "part.stl").exists()  # left for the sweeper
    assert (await authenticated_client.get("/api/studio/session")).status_code == 204


async def test_scan_attaches_returned_file(db_session, seed_file, exchange, rds) -> None:
    model, rev, f = await _model(db_session, seed_file, "st-scan")
    await _enable()
    _plant(rds, "tokA", model, rev, f)
    pubsub = rds.pubsub()
    pubsub.subscribe("tdmm:events")
    out = _drop(exchange / "tokA" / "result.3mf", b"3mf-bytes-1")
    _drop(exchange / "tokA" / "in" / "part.stl")  # input copy is never scanned
    _drop(exchange / "tokA" / "fresh.3mf", age=0)  # unstable
    _drop(exchange / "tokA" / "x.3mf.tmp")  # temp name

    scan_studio_exchange()

    assert not out.exists() and (exchange / "tokA" / ".imported" / "result.3mf").exists()
    assert (exchange / "tokA" / "fresh.3mf").exists() and (exchange / "tokA" / "x.3mf.tmp").exists()
    assert (exchange / "tokA" / "in" / "part.stl").exists()
    names = set(
        (
            await db_session.execute(select(File.rel_path).where(File.revision_id == rev.id))
        ).scalars()
    )
    assert names == {"part.stl", "result.3mf"}
    new = (await db_session.execute(select(File).where(File.rel_path == "result.3mf"))).scalar_one()
    events = []
    while (m := pubsub.get_message(timeout=1)) is not None:
        if m["type"] == "message":
            events.append(json.loads(m["data"]))
    ev = [e for e in events if e["type"] == "studio.file_returned"]
    assert ev == [
        {
            "type": "studio.file_returned",
            "model_slug": model.slug,
            "model_id": model.id,
            "file_id": new.id,
            "name": "result.3mf",
            "sliced": False,
        }
    ]


async def test_scan_sliced_and_duplicate(db_session, seed_file, exchange, rds) -> None:
    model, rev, f = await _model(db_session, seed_file, "st-dup")
    await _enable()
    _plant(rds, "tokB", model, rev, f)
    _drop(exchange / "tokB" / "plate.gcode.3mf", b"sliced-bytes")
    _drop(exchange / "tokB" / "same.stl", b"solid x\nendsolid x\n")  # == seeded blob

    scan_studio_exchange()

    imported = exchange / "tokB" / ".imported"
    assert (imported / "plate.gcode.3mf").exists() and (imported / "same.stl").exists()
    rows = (
        await db_session.execute(select(File.rel_path).where(File.revision_id == rev.id))
    ).scalars()
    assert set(rows) == {"part.stl", "plate.gcode.3mf"}


async def test_scan_renames_on_name_clash(db_session, seed_file, exchange, rds) -> None:
    model, rev, f = await _model(db_session, seed_file, "st-clash")
    await _enable()
    _plant(rds, "tokC", model, rev, f)
    _drop(exchange / "tokC" / "part.stl", b"solid y\nendsolid y\n")

    scan_studio_exchange()

    rows = (
        await db_session.execute(select(File.rel_path).where(File.revision_id == rev.id))
    ).scalars()
    assert set(rows) == {"part.stl", "part (studio).stl"}


async def test_scan_unknown_token_and_unsupported(db_session, seed_file, exchange, rds) -> None:
    model, rev, f = await _model(db_session, seed_file, "st-fail")
    await _enable()
    _plant(rds, "tokD", model, rev, f)
    _drop(exchange / "ghost" / "a.3mf")
    _drop(exchange / "tokD" / "notes.txt")

    scan_studio_exchange()

    assert (exchange / "ghost" / ".failed" / "a.3mf").exists()
    assert (exchange / "tokD" / ".failed" / "notes.txt").exists()


async def test_scan_noop_when_disabled(db_session, exchange) -> None:
    out = _drop(exchange / "tokE" / "a.3mf")
    scan_studio_exchange()
    assert out.exists()


async def test_sweeper_removes_expired_dirs(db_session, exchange) -> None:
    await _enable()
    old, new = exchange / "oldtok", exchange / "newtok"
    _drop(old / "in" / "a.stl")
    _drop(new / "in" / "a.stl")
    past = time.time() - ss.SESSION_TTL_S - 60
    os.utime(old, (past, past))

    scan_studio_exchange()

    assert not old.exists() and new.exists()
