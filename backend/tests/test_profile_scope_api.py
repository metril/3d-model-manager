"""Printer/nozzle/layer scope on `GET /profiles`, `/recipes`, `/slicer-pipelines`
and the persisted/live nozzle on the printer APIs."""

from __future__ import annotations

import json

import httpx
import pytest
import redis as redis_lib
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Printer
from app.models.enums import PrinterKind
from app.printers.base import state_key

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")

A1M = "Bambu Lab A1 mini"


async def _printer(db: AsyncSession, model: str, nozzle: float | None = None, n: int = 1) -> int:
    p = Printer(
        name=f"{model}-{n}",
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial=f"S{model}{n}",
        access_code_enc="x",
        model=model,
        nozzle_mm=nozzle,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.id


async def _profile(client: httpx.AsyncClient, kind: str, name: str, data: dict | None = None):
    r = await client.post("/api/profiles", json={"name": name, "kind": kind, "data": data or {}})
    assert r.status_code == 201, r.text
    return r.json()


def _compat(model: str, noz: float) -> dict:
    return {"compatible_printers": [f"{model} {noz} nozzle"]}


@pytest.fixture
async def seeded(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    ids = {
        "pr_a1m": (await _profile(c, "printer", "A1M printer", {"printer_model": A1M}))["id"],
        "p_a1m04": (
            await _profile(
                c, "process", "0.20mm A1M 0.4", {**_compat(A1M, 0.4), "layer_height": "0.2"}
            )
        )["id"],
        "p_a1m04_16": (
            await _profile(
                c, "process", "0.16mm A1M 0.4", {**_compat(A1M, 0.4), "layer_height": "0.16"}
            )
        )["id"],
        "p_a1m02": (
            await _profile(
                c, "process", "0.20mm A1M 0.2", {**_compat(A1M, 0.2), "layer_height": "0.2"}
            )
        )["id"],
        "p_p1s": (
            await _profile(
                c,
                "process",
                "0.20mm P1S 0.4",
                {**_compat("Bambu Lab P1S", 0.4), "layer_height": "0.2"},
            )
        )["id"],
        "p_multi": (
            await _profile(
                c,
                "process",
                "0.20mm multi",
                {
                    "compatible_printers": [
                        f"{A1M} 0.4 nozzle",
                        "Bambu Lab P1S 0.4 nozzle",
                    ],
                    "layer_height": "0.2",
                },
            )
        )["id"],
        "f_any": (await _profile(c, "filament", "Hand PLA"))["id"],
    }
    return ids


def _names(body) -> set[str]:
    return {i["name"] for i in body["items"]}


async def test_wrapper_shape_and_derived_fields(authenticated_client, seeded):
    r = await authenticated_client.get("/api/profiles?kind=process")
    body = r.json()
    assert body["total"] == 5 and len(body["items"]) == 5 and body["nozzle_source"] is None
    row = next(i for i in body["items"] if i["name"] == "0.20mm A1M 0.4")
    assert row["printer_model_key"] == "a1mini"
    assert row["compat"] == [{"model_key": "a1mini", "nozzle_mm": 0.4}]
    assert row["layer_height_mm"] == 0.2


async def test_q_filters_in_sql(authenticated_client, seeded):
    body = (await authenticated_client.get("/api/profiles?q=p1s")).json()
    assert _names(body) == {"0.20mm P1S 0.4"} and body["total"] == 1
    assert (await authenticated_client.get("/api/profiles?q=%25")).json()["total"] == 0


async def test_scope_by_printer_assumed_nozzle(authenticated_client, db_session, seeded):
    pid = await _printer(db_session, "A1 mini")
    body = (await authenticated_client.get(f"/api/profiles?printer_id={pid}")).json()
    assert body["nozzle_source"] == "assumed" and body["nozzle_mm"] == 0.4
    names = _names(body)
    assert {"0.20mm A1M 0.4", "0.16mm A1M 0.4", "0.20mm multi", "Hand PLA", "A1M printer"} <= names
    assert not names & {"0.20mm A1M 0.2", "0.20mm P1S 0.4"}
    assert body["total"] == 7


async def test_nozzle_resolution_live_then_last_seen_then_override(
    authenticated_client, db_session, redis_url, seeded
):
    pid = await _printer(db_session, "A1 mini", nozzle=0.2)
    url = f"/api/profiles?kind=process&printer_id={pid}"
    body = (await authenticated_client.get(url)).json()
    assert body["nozzle_source"] == "last_seen"
    assert _names(body) == {"0.20mm A1M 0.2"}

    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), json.dumps({"connected": True, "nozzle_mm": 0.4}))
    try:
        body = (await authenticated_client.get(url)).json()
        assert body["nozzle_source"] == "live" and "0.20mm A1M 0.4" in _names(body)
        body = (await authenticated_client.get(url + "&nozzle_mm=0.2")).json()
        assert body["nozzle_source"] == "override" and _names(body) == {"0.20mm A1M 0.2"}
    finally:
        c.delete(state_key(pid))
        c.close()


async def test_layer_filter_and_show_all(authenticated_client, db_session, seeded):
    pid = await _printer(db_session, "A1M")  # alias spelling
    url = f"/api/profiles?kind=process&printer_id={pid}&layer_height_mm=0.16"
    assert _names((await authenticated_client.get(url)).json()) == {"0.16mm A1M 0.4"}
    body = (await authenticated_client.get(url + "&show_all=1")).json()
    assert len(body["items"]) == 5
    flags = {i["name"]: i["in_scope"] for i in body["items"]}
    assert flags["0.16mm A1M 0.4"] is True and flags["0.20mm P1S 0.4"] is False
    reason = next(i for i in body["items"] if i["name"] == "0.20mm P1S 0.4")["scope_reason"]
    assert reason


async def test_unknown_printer_404(authenticated_client, seeded):
    assert (await authenticated_client.get("/api/profiles?printer_id=9999")).status_code == 404


async def test_patch_data_rederives_but_keeps_user_columns(authenticated_client, seeded):
    pid = seeded["f_any"]
    r = await authenticated_client.patch(
        f"/api/profiles/{pid}",
        json={"printer_model": "mine", "nozzle_mm": 1.0, "data": _compat("Bambu Lab P1S", 0.6)},
    )
    body = r.json()
    assert body["printer_model"] == "mine" and body["nozzle_mm"] == 1.0
    assert body["compat"] == [{"model_key": "p1s", "nozzle_mm": 0.6}]


async def test_recipes_compatible_only_and_q(authenticated_client, db_session, seeded):
    c = authenticated_client

    async def recipe(name: str, process: str) -> int:
        r = await c.post(
            "/api/recipes",
            json={
                "name": name,
                "printer_profile_id": seeded["pr_a1m"],
                "process_profile_id": seeded[process],
                "filament_profile_ids": [seeded["f_any"]],
                "bed_type": "textured_pei",
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    ok = await recipe("ok recipe", "p_a1m04")
    bad = await recipe("wrong nozzle", "p_a1m02")
    pid = await _printer(db_session, "A1 mini", nozzle=0.4)

    allr = (await c.get(f"/api/recipes?printer_id={pid}")).json()
    flags = {r["id"]: r for r in allr}
    assert flags[ok]["compatible"] is True
    assert flags[bad]["compatible"] is False and "process" in flags[bad]["incompatible_reason"]

    only = (await c.get(f"/api/recipes?printer_id={pid}&compatible_only=1")).json()
    assert [r["id"] for r in only] == [ok]
    assert [r["id"] for r in (await c.get("/api/recipes?q=WRONG")).json()] == [bad]
    layered = (await c.get(f"/api/recipes?printer_id={pid}&layer_height_mm=0.16")).json()
    assert all(not r["compatible"] for r in layered)
    # Unscoped call keeps the old shape (no verdict).
    assert all(r["compatible"] is None for r in (await c.get("/api/recipes")).json())


async def test_pipelines_printer_id_and_q(authenticated_client, db_session, seeded):
    c = authenticated_client
    a1 = await _printer(db_session, "A1 mini", nozzle=0.4, n=1)
    other = await _printer(db_session, "P1S", nozzle=0.4, n=2)

    async def recipe(name: str, process: str) -> int:
        r = await c.post(
            "/api/recipes",
            json={
                "name": name,
                "printer_profile_id": seeded["pr_a1m"],
                "process_profile_id": seeded[process],
                "filament_profile_ids": [],
                "bed_type": "textured_pei",
            },
        )
        return r.json()["id"]

    good = await recipe("r-good", "p_a1m04")
    bad = await recipe("r-bad", "p_a1m02")
    for name, rid, steps in (
        ("pipe-good", good, []),
        ("pipe-bad", bad, []),
        ("pipe-queued", bad, [{"kind": "queue", "printer_id": a1}]),
    ):
        r = await c.post(
            "/api/slicer-pipelines", json={"name": name, "recipe_id": rid, "steps": steps}
        )
        assert r.status_code == 201, r.text

    names = lambda body: {p["name"] for p in body}  # noqa: E731
    assert names((await c.get(f"/api/slicer-pipelines?printer_id={a1}")).json()) == {
        "pipe-good",
        "pipe-queued",
    }
    assert names((await c.get(f"/api/slicer-pipelines?printer_id={a1}&q=QUEUED")).json()) == {
        "pipe-queued"
    }
    # The A1-mini recipes' printer profile is for another model than `other`.
    assert names((await c.get(f"/api/slicer-pipelines?printer_id={other}")).json()) == set()
    assert len((await c.get("/api/slicer-pipelines")).json()) == 3


async def test_printer_out_has_nozzle(authenticated_client, db_session, printer_enabled, redis_url):
    pid = await _printer(db_session, "A1 mini", nozzle=0.2)
    assert (await authenticated_client.get(f"/api/printers/{pid}")).json()["nozzle_mm"] == 0.2
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), json.dumps({"connected": True, "nozzle_mm": 0.6}))
    try:
        status = (await authenticated_client.get(f"/api/printers/{pid}/status")).json()
        assert status["nozzle_mm"] == 0.6
    finally:
        c.delete(state_key(pid))
        c.close()
