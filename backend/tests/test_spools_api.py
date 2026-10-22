"""Spools CRUD + manual usage + AMS slot assignment (P5 spool tray plan,
Task 6)."""

from __future__ import annotations

import json

import httpx
import pytest
import redis as redis_lib
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Printer
from app.models.enums import PrinterKind
from app.printers.base import state_key

pytestmark = pytest.mark.usefixtures("library_root", "printer_enabled")

SPOOL = {"material_type": "PLA", "brand": "Acme", "color_hex": "#FF0000", "color_name": "Red"}


async def _create_spool(client: httpx.AsyncClient, **overrides) -> dict:
    body = {**SPOOL, **overrides}
    r = await client.post("/api/spools", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _printer(db: AsyncSession, name: str = "P1") -> Printer:
    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.1", serial=name, access_code_enc="x"
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def test_crud_round_trip(authenticated_client: httpx.AsyncClient):
    created = await _create_spool(authenticated_client)
    sid = created["id"]
    assert created["remaining_g"] == 1000.0
    assert created["low_stock"] is False
    assert created["slot"] is None

    got = (await authenticated_client.get(f"/api/spools/{sid}")).json()
    assert got["id"] == sid and got["usage"] == [] and got["series"][0]["remaining_g"] == 1000.0

    patched = (
        await authenticated_client.patch(f"/api/spools/{sid}", json={"weight_used_g": 200})
    ).json()
    assert patched["weight_used_g"] == 200 and patched["remaining_g"] == 800.0

    archived = (
        await authenticated_client.patch(f"/api/spools/{sid}", json={"archived": True})
    ).json()
    assert archived["archived_at"] is not None
    assert (await authenticated_client.get("/api/spools")).json() == []
    assert any(
        s["id"] == sid for s in (await authenticated_client.get("/api/spools?archived=true")).json()
    )

    r = await authenticated_client.delete(f"/api/spools/{sid}")
    assert r.status_code == 204
    assert (await authenticated_client.get(f"/api/spools/{sid}")).status_code == 404


async def test_low_stock_filter(authenticated_client: httpx.AsyncClient):
    await _create_spool(authenticated_client, label_weight_g=1000, low_stock_g=900)
    low = await _create_spool(authenticated_client, weight_used_g=950, low_stock_g=100)
    out = (await authenticated_client.get("/api/spools?low_stock=true")).json()
    assert [s["id"] for s in out] == [low["id"]]


async def test_color_hex_validation_422(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.post("/api/spools", json={**SPOOL, "color_hex": "red"})
    assert r.status_code == 422


async def test_duplicate_tray_uuid_409(authenticated_client: httpx.AsyncClient):
    await _create_spool(authenticated_client, tray_uuid="abc123")
    r = await authenticated_client.post("/api/spools", json={**SPOOL, "tray_uuid": "abc123"})
    assert r.status_code == 409


async def test_viewer_cannot_write_spool(
    authenticated_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
):
    created = await _create_spool(authenticated_client)
    sid = created["id"]
    assert (await viewer_client.post("/api/spools", json=SPOOL)).status_code == 403
    assert (
        await viewer_client.patch(f"/api/spools/{sid}", json={"weight_used_g": 1})
    ).status_code == 403
    assert (await viewer_client.delete(f"/api/spools/{sid}")).status_code == 403
    assert (
        await viewer_client.post(f"/api/spools/{sid}/usage", json={"grams": 5})
    ).status_code == 403


async def test_manual_usage(authenticated_client: httpx.AsyncClient):
    created = await _create_spool(authenticated_client)
    sid = created["id"]
    r = await authenticated_client.post(f"/api/spools/{sid}/usage", json={"grams": 50})
    assert r.status_code == 201
    assert r.json()["source"] == "manual"
    updated = (await authenticated_client.get(f"/api/spools/{sid}")).json()
    assert updated["weight_used_g"] == 50 and updated["remaining_g"] == 950.0
    assert len(updated["usage"]) == 1

    bad = await authenticated_client.post(f"/api/spools/{sid}/usage", json={"grams": 0})
    assert bad.status_code == 422


async def test_slot_requires_control(
    authenticated_client: httpx.AsyncClient,
    member_client: httpx.AsyncClient,
    make_group,
    member,
    db_session: AsyncSession,
):
    p = await _printer(db_session)
    spool = await _create_spool(authenticated_client)
    await make_group("print-only", members=[member], printer_access={p.id: (True, False, False)})

    r = await member_client.put(f"/api/printers/{p.id}/slots/0/0", json={"spool_id": spool["id"]})
    assert r.status_code == 403

    await make_group(
        "controllers",
        members=[member],
        permissions=["printers:control"],
        printer_access={p.id: (True, True, False)},
    )
    r2 = await member_client.put(f"/api/printers/{p.id}/slots/0/0", json={"spool_id": spool["id"]})
    assert r2.status_code == 200
    got = (await authenticated_client.get(f"/api/spools/{spool['id']}")).json()
    assert got["slot"] == {"printer_id": p.id, "ams_unit": 0, "slot": 0}


async def test_slot_validation(authenticated_client: httpx.AsyncClient, db_session: AsyncSession):
    p = await _printer(db_session)
    spool = await _create_spool(authenticated_client)
    bad = await authenticated_client.put(
        f"/api/printers/{p.id}/slots/4/0", json={"spool_id": spool["id"]}
    )
    assert bad.status_code == 422
    bad2 = await authenticated_client.put(
        f"/api/printers/{p.id}/slots/255/0", json={"spool_id": spool["id"]}
    )
    assert bad2.status_code == 422
    ok = await authenticated_client.put(
        f"/api/printers/{p.id}/slots/255/254", json={"spool_id": spool["id"]}
    )
    assert ok.status_code == 200


async def test_slot_clear(authenticated_client: httpx.AsyncClient, db_session: AsyncSession):
    p = await _printer(db_session)
    spool = await _create_spool(authenticated_client)
    await authenticated_client.put(
        f"/api/printers/{p.id}/slots/0/0", json={"spool_id": spool["id"]}
    )
    r = await authenticated_client.put(f"/api/printers/{p.id}/slots/0/0", json={"spool_id": None})
    assert r.status_code == 200
    got = (await authenticated_client.get(f"/api/spools/{spool['id']}")).json()
    assert got["slot"] is None


async def test_status_trays_carry_spool(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, redis_url: str
):
    p = await _printer(db_session)
    spool = await _create_spool(authenticated_client)
    await authenticated_client.put(
        f"/api/printers/{p.id}/slots/0/0", json={"spool_id": spool["id"]}
    )

    c = redis_lib.Redis.from_url(redis_url)
    try:
        c.set(
            state_key(p.id),
            json.dumps(
                {
                    "gcode_state": "IDLE",
                    "trays": [{"slot": 0, "ams_unit": 0, "tray": 0, "color": "#FF0000"}],
                }
            ),
        )

        status = (await authenticated_client.get(f"/api/printers/{p.id}/status")).json()
        assert status["trays"][0]["spool"]["id"] == spool["id"]

        overview = (await authenticated_client.get("/api/printers/overview")).json()
        item = next(i for i in overview["printers"] if i["printer"]["id"] == p.id)
        assert item["status"]["trays"][0]["spool"]["id"] == spool["id"]
    finally:
        # Redis isn't truncated between tests (unlike the Postgres
        # container) -- clean up so a later test reusing this printer's id
        # (ids reset per test via RESTART IDENTITY) doesn't see stale state.
        c.delete(state_key(p.id))
        c.close()


async def test_spool_detail_usage_and_series(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    created = await _create_spool(authenticated_client)
    sid = created["id"]
    await authenticated_client.post(f"/api/spools/{sid}/usage", json={"grams": 100})
    await authenticated_client.post(f"/api/spools/{sid}/usage", json={"grams": 50})
    detail = (await authenticated_client.get(f"/api/spools/{sid}")).json()
    assert [u["grams"] for u in detail["usage"]] == [100, 50]
    assert [p["remaining_g"] for p in detail["series"]] == [1000.0, 900.0, 850.0]
