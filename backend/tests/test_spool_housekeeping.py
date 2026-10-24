"""T5 spool housekeeping: locations, suppliers, colour catalog, bulk ops,
CSV round-trip, labels and HA location sensors."""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.models.inventory import LocationHaSensor
from app.services import location_sensors
from app.services.ha_config import HaConfig
from app.services.housekeeping import load_default_catalog
from app.tasks import base

PNG = b"\x89PNG\r\n\x1a\n"


async def _spool(c: httpx.AsyncClient, **kw) -> dict:
    body = {"material_type": "PLA", "brand": "Acme", "color_hex": "#FF0000", "color_name": "Red"}
    r = await c.post("/api/spools", json={**body, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _supplier(c: httpx.AsyncClient, name: str = "Filaments R Us", **kw) -> dict:
    r = await c.post("/api/suppliers", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- locations


async def test_location_crud_and_spool_link(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    loc = (await c.post("/api/storage-locations", json={"name": "Shelf A"})).json()
    assert (await c.post("/api/storage-locations", json={"name": " shelf a "})).status_code == 409
    sp = await _spool(c, location_id=loc["id"])
    assert sp["location_id"] == loc["id"] and sp["location"] == "Shelf A"
    assert (
        await c.post("/api/spools", json={"material_type": "PLA", "location_id": 999})
    ).status_code == 404

    listed = (await c.get("/api/storage-locations")).json()
    assert listed[0]["spool_count"] == 1 and listed[0]["sensors"] == []
    patched = (
        await c.patch(f"/api/storage-locations/{loc['id']}", json={"identifier": "A1"})
    ).json()
    assert patched["identifier"] == "A1"

    assert (await c.delete(f"/api/storage-locations/{loc['id']}")).status_code == 204
    got = (await c.get(f"/api/spools/{sp['id']}")).json()
    assert got["location_id"] is None and got["location"] is None


# ---------------------------------------------------------------- suppliers


async def test_supplier_crud_and_replace(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    a = await _supplier(c, "Alpha", website="https://a.example")
    b = await _supplier(c, "Beta")
    assert (await c.post("/api/suppliers", json={"name": " alpha"})).status_code == 409
    assert (await c.patch(f"/api/suppliers/{b['id']}", json={"note": "n"})).json()["note"] == "n"

    sp = await _spool(c)
    url = f"/api/spools/{sp['id']}/suppliers"
    r = await c.put(
        url,
        json=[
            {
                "supplier_id": a["id"],
                "article_number": "X1",
                "quoted_price_per_kg": 19.5,
                "is_purchase_source": True,
            },
            {"supplier_id": b["id"]},
        ],
    )
    assert r.status_code == 200, r.text
    assert [s["supplier_name"] for s in r.json()] == ["Alpha", "Beta"]
    assert r.json()[0]["quoted_price_per_kg"] == 19.5
    detail = (await c.get(f"/api/spools/{sp['id']}")).json()
    assert [s["supplier_id"] for s in detail["suppliers"]] == [a["id"], b["id"]]
    assert (await c.get("/api/suppliers")).json()[0]["spool_count"] == 1

    bad = [
        {"supplier_id": a["id"], "is_purchase_source": True},
        {"supplier_id": b["id"], "is_purchase_source": True},
    ]
    assert (await c.put(url, json=bad)).status_code == 400
    assert (await c.put(url, json=[{"supplier_id": 9999}])).status_code == 404
    assert (await c.put(url, json=[{"supplier_id": a["id"]}] * 2)).status_code == 400
    assert (await c.put(url, json=[])).json() == []

    assert (await c.delete(f"/api/suppliers/{a['id']}")).status_code == 204


# ------------------------------------------------------------------ catalog


async def test_catalog_lookup_search_nearest_reset(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    c = authenticated_client
    for name, hex_, mat in (
        ("Jade White", "#FFFFFF", "PLA Basic"),
        ("Red", "#C12E1F", "PLA Basic"),
        ("Red", "#B02020", "PLA Matte"),
    ):
        r = await c.post(
            "/api/color-catalog",
            json={"manufacturer": "Bambu Lab", "color_name": name, "hex": hex_, "material": mat},
        )
        assert r.status_code == 201, r.text
    dup = {
        "manufacturer": "Bambu Lab",
        "color_name": "Red",
        "hex": "#000000",
        "material": "PLA Basic",
    }
    assert (await c.post("/api/color-catalog", json=dup)).status_code == 409

    hit = (
        await c.get(
            "/api/color-catalog/lookup",
            params={"manufacturer": "bambu lab", "name": "red", "material": "PLA Matte"},
        )
    ).json()
    assert hit["hex"] == "#B02020"
    assert (
        await c.get("/api/color-catalog/lookup", params={"manufacturer": "x", "name": "y"})
    ).status_code == 404
    assert len((await c.get("/api/color-catalog/search", params={"q": "red"})).json()) == 2
    assert len((await c.get("/api/color-catalog/by-material/pla basic")).json()) == 2
    near = (await c.get("/api/color-catalog/nearest", params={"hex": "#FEFEFE"})).json()
    assert near["color_name"] == "Jade White" and near["delta_e"] < 0.05

    r = await c.post("/api/color-catalog/reset")
    assert r.status_code == 200
    total = len(load_default_catalog())
    # a custom row that collides with a default key keeps the custom row
    assert r.json()["restored"] < total
    entries = (await c.get("/api/color-catalog")).json()
    assert sum(e["is_default"] for e in entries) == r.json()["restored"]
    assert len(entries) == r.json()["restored"] + 3
    assert any(e["color_name"] == "Red" and e["material"] == "PLA Matte" for e in entries)
    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "color_catalog.reset")))
        .scalars()
        .all()
    )
    assert len(audit) == 1


async def test_catalog_reset_is_admin_only(viewer_client: httpx.AsyncClient):
    assert (await viewer_client.post("/api/color-catalog/reset")).status_code == 403


# --------------------------------------------------------------------- bulk


async def test_bulk_update_per_id_errors_and_not_found(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    loc = (await c.post("/api/storage-locations", json={"name": "Box"})).json()
    a, b = await _spool(c), await _spool(c, weight_used_g=100, label_weight_g=200)
    r = (
        await c.post(
            "/api/spools/bulk-update",
            json={
                "ids": [a["id"], b["id"], 999],
                "update": {"location_id": loc["id"], "brand": "Zed"},
            },
        )
    ).json()
    assert r["not_found"] == [999] and all(x["ok"] for x in r["results"])
    assert (await c.get(f"/api/spools/{a['id']}")).json()["brand"] == "Zed"
    assert (await c.get(f"/api/spools/{b['id']}")).json()["location"] == "Box"

    r = (
        await c.post(
            "/api/spools/bulk-update",
            json={"ids": [a["id"], b["id"]], "update": {"weight_used_g": 250}},
        )
    ).json()
    by = {x["id"]: x for x in r["results"]}
    assert by[a["id"]]["ok"] is True  # 250 <= 1000
    assert by[b["id"]]["ok"] is False and "exceeds" in by[b["id"]]["error"]
    assert (
        await c.post(
            "/api/spools/bulk-update", json={"ids": [a["id"]], "update": {"color_hex": "nope"}}
        )
    ).status_code == 422
    assert (
        await c.post(
            "/api/spools/bulk-update", json={"ids": [a["id"]], "update": {"location_id": 12345}}
        )
    ).status_code == 404


async def test_bulk_archive_restore_delete(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    c = authenticated_client
    a, b = await _spool(c), await _spool(c)
    r = (await c.post("/api/spools/bulk-archive", json={"ids": [a["id"], b["id"], 77]})).json()
    assert r["not_found"] == [77] and len(r["results"]) == 2
    assert (await c.get("/api/spools")).json() == []
    await c.post("/api/spools/bulk-restore", json={"ids": [a["id"]]})
    assert [s["id"] for s in (await c.get("/api/spools")).json()] == [a["id"]]

    await c.post(f"/api/spools/{b['id']}/usage", json={"grams": 5})
    r = (await c.post("/api/spools/bulk-delete", json={"ids": [a["id"], b["id"]]})).json()
    by = {x["id"]: x for x in r["results"]}
    assert by[a["id"]]["ok"] is True
    assert by[b["id"]]["ok"] is False and "usage" in by[b["id"]]["error"]
    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "spool.bulk_delete")))
        .scalars()
        .all()
    )
    assert len(audit) == 1 and audit[0].payload["deleted"] == [a["id"]]


# ---------------------------------------------------------------------- CSV


async def _import(c, csv_text: str, dry_run: bool = True):
    return await c.post(
        "/api/spools/import",
        params={"dry_run": str(dry_run).lower()},
        files={"file": ("s.csv", csv_text.encode(), "text/csv")},
    )


async def test_csv_round_trip_and_dry_run_writes_nothing(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    loc = (await c.post("/api/storage-locations", json={"name": "Shelf A"})).json()
    sup = await _supplier(c, "Alpha")
    s1 = await _spool(c, location_id=loc["id"], weight_used_g=120, cost_per_kg=21.5, tray_uuid="T1")
    s2 = await _spool(
        c, material_type="PETG", brand=None, color_name="=SUM(A1)", color_hex="#00FF00"
    )
    await c.put(
        f"/api/spools/{s1['id']}/suppliers",
        json=[{"supplier_id": sup["id"], "is_purchase_source": True}],
    )

    exp = await c.get("/api/spools/export.csv")
    assert exp.headers["content-type"].startswith("text/csv")
    assert "attachment" in exp.headers["content-disposition"]
    text = exp.text
    assert text.splitlines()[0].startswith("id,material,brand,color_name,rgba,label_weight")
    assert "'=SUM(A1)" in text  # formula-injection guard

    # unchanged export imports as pure updates
    r = (await _import(c, text)).json()
    assert r["summary"] == {"create": 0, "update": 2, "error": 0, "skipped": 0}, r
    assert r["token"]

    # edit one row, add a brand new one (no id), one bad row
    lines = text.splitlines()
    edited = lines[1].replace(",120,", ",250,")
    assert edited != lines[1]
    new_row = ",ABS,Acme,Blue,0000ffff,1000,0,,,,,,Shelf B,,,Nobody,"
    bad_row = ",,Acme,Nameless,,,,,,,,,,,,,"
    body = "\n".join([lines[0], edited, lines[2], new_row, bad_row]) + "\n"
    dry = (await _import(c, body)).json()
    assert dry["summary"] == {"create": 1, "update": 2, "error": 1, "skipped": 0}, dry
    by_line = {row["line"]: row for row in dry["rows"]}
    assert by_line[2]["action"] == "update" and by_line[2]["spool_id"] == s1["id"]
    assert any("unknown supplier" in w for w in by_line[4]["warnings"])
    assert any("Shelf B" in w for w in by_line[4]["warnings"])
    assert by_line[5]["action"] == "error" and by_line[5]["errors"]
    assert any("Nobody" in w for w in dry["warnings"])

    # dry run wrote nothing
    assert len((await c.get("/api/spools")).json()) == 2
    assert (await c.get(f"/api/spools/{s1['id']}")).json()["weight_used_g"] == 120
    assert len((await c.get("/api/storage-locations")).json()) == 1

    res = (await c.post("/api/spools/import/commit", json={"token": dry["token"]})).json()
    assert res == {"created": 1, "updated": 2, "errors": 1}
    spools = {s["id"]: s for s in (await c.get("/api/spools")).json()}
    assert len(spools) == 3
    assert spools[s1["id"]]["weight_used_g"] == 250 and spools[s1["id"]]["location"] == "Shelf A"
    assert spools[s2["id"]]["color_name"] == "=SUM(A1)"
    new = next(s for sid, s in spools.items() if sid not in (s1["id"], s2["id"]))
    assert new["color_hex"] == "#0000FF" and new["location"] == "Shelf B"
    assert (await c.get(f"/api/spools/{s1['id']}")).json()["suppliers"][0][
        "supplier_name"
    ] == "Alpha"
    # token is single-use
    assert (
        await c.post("/api/spools/import/commit", json={"token": dry["token"]})
    ).status_code == 410


async def test_csv_import_rejects_tray_conflict_and_expired_token(
    authenticated_client: httpx.AsyncClient,
):
    c = authenticated_client
    assert (await c.post("/api/spools/import/commit", json={"token": "nope"})).status_code == 410
    csv_text = "material,brand,tray_uuid\nPLA,Acme,U1\nPETG,Acme,U1\n"
    dry = (await _import(c, csv_text)).json()
    assert dry["summary"]["create"] == 2
    r = await c.post("/api/spools/import/commit", json={"token": dry["token"]})
    assert r.status_code == 409
    assert (await c.get("/api/spools")).json() == []  # rolled back


async def test_csv_import_non_dry_run_and_empty(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    r = await _import(c, "material,brand\nPLA,Acme\n", dry_run=False)
    assert r.json() == {"created": 1, "updated": 0, "errors": 0}
    empty = (await _import(c, "")).json()
    assert empty["token"] is None and empty["warnings"]


# ------------------------------------------------------------------- labels


async def test_labels_pdf_png_zip_preview(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    ids = [(await _spool(c))["id"] for _ in range(3)]
    base_body = {"ids": ids, "template": "roomy", "page": "a4"}

    pdf = await c.post("/api/spools/labels", json={**base_body, "format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and len(pdf.content) > 1000

    png = await c.post("/api/spools/labels", json={**base_body, "format": "png"})
    assert png.headers["content-type"] == "image/png" and png.content.startswith(PNG)

    roll = await c.post(
        "/api/spools/labels", json={**base_body, "page": "roll_62mm", "format": "png"}
    )
    assert roll.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(roll.content)).namelist()
    assert len(names) == 3

    tight = await c.post(
        "/api/spools/labels",
        json={**base_body, "template": "tight", "page": "letter", "format": "png"},
    )
    assert tight.content.startswith(PNG)

    prev = await c.post("/api/spools/labels/preview", json=base_body)
    assert prev.content.startswith(PNG) and len(prev.content) > 2000
    assert (
        await c.post("/api/spools/labels", json={**base_body, "ids": [ids[0], 999]})
    ).status_code == 404


async def test_label_qr_encodes_spool_url(authenticated_client: httpx.AsyncClient, monkeypatch):
    seen: list[str] = []
    real = location_sensors.__name__  # keep import used
    assert real
    from app.services import spool_labels

    orig = spool_labels._qr
    monkeypatch.setattr(spool_labels, "_qr", lambda url: (seen.append(url), orig(url))[1])
    sp = await _spool(authenticated_client)
    r = await authenticated_client.post(
        "/api/spools/labels", json={"ids": [sp["id"]], "format": "pdf"}
    )
    assert r.status_code == 200
    assert seen and seen[0].endswith(f"/inventory/spools?spool={sp['id']}")


# --------------------------------------------------------------- HA sensors


class _FakeHa:
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states

    def state(self, entity_id: str):
        v = self.states.get(entity_id)
        if v is None:
            return None
        return {
            "state": v,
            "last_changed": "2026-10-24T10:00:00+00:00",
            "attributes": {"unit_of_measurement": "%", "device_class": "humidity"},
        }

    def close(self) -> None:
        pass


async def test_ha_sensor_alert_transition_emits_once(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    loc = (await c.post("/api/storage-locations", json={"name": "Dry box"})).json()
    r = await c.post(
        f"/api/storage-locations/{loc['id']}/sensors",
        json={
            "name": "Humidity",
            "entity_id": "sensor.h",
            "kind": "humidity",
            "alert_above": 50,
            "notify_on_alert": True,
        },
    )
    assert r.status_code == 201, r.text
    quiet = await c.post(
        f"/api/storage-locations/{loc['id']}/sensors",
        json={"name": "Quiet", "entity_id": "sensor.q", "alert_above": 10},
    )
    assert quiet.status_code == 201

    cfg = HaConfig(None, None, None, "http://ha", "tok")
    states = {"sensor.h": "40", "sensor.q": "99"}
    emitted: list[tuple] = []

    def tick():
        with base.sync_session() as s:
            return location_sensors.poll_once(
                s,
                cfg,
                client_factory=lambda *_: _FakeHa(states),
                emit=lambda *a, **k: emitted.append(a) or True,
            )

    import asyncio

    run = lambda: asyncio.get_running_loop().run_in_executor(None, tick)  # noqa: E731
    assert (await run())["alerts"] == 0 and emitted == []
    states["sensor.h"] = "62"
    assert (await run())["alerts"] == 1
    assert emitted[0][0] == "location_sensor_alert"
    assert emitted[0][2]["location"] == "Dry box" and emitted[0][2]["direction"] == "above"
    await run()  # still above: no second event
    assert len(emitted) == 1
    states["sensor.h"] = "45"
    await run()  # recovers
    states["sensor.h"] = "70"
    await run()  # re-enters: second event
    assert len(emitted) == 2

    readings = (await c.get(f"/api/storage-locations/{loc['id']}/readings")).json()
    h = next(x for x in readings if x["name"] == "Humidity")
    assert h["state"] == "70" and h["alerting"] is True and h["unit"] == "%"
    # `quiet` has notify_on_alert off: alerting tracked, never emitted
    assert next(x for x in readings if x["name"] == "Quiet")["alerting"] is True


async def test_ha_entities_503_when_unconfigured(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.get("/api/storage-locations/sensors/entities")
    assert r.status_code == 503


async def test_sensor_patch_delete(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    c = authenticated_client
    loc = (await c.post("/api/storage-locations", json={"name": "L"})).json()
    s = (
        await c.post(
            f"/api/storage-locations/{loc['id']}/sensors",
            json={"name": "T", "entity_id": "sensor.t"},
        )
    ).json()
    p = (
        await c.patch(f"/api/storage-locations/sensors/{s['id']}", json={"alert_below": 5.5})
    ).json()
    assert p["alert_below"] == 5.5
    assert (await c.get("/api/storage-locations")).json()[0]["sensors"][0]["name"] == "T"
    assert (await c.delete(f"/api/storage-locations/sensors/{s['id']}")).status_code == 204
    assert (await db_session.execute(select(LocationHaSensor))).scalars().all() == []


@pytest.mark.parametrize("path", ["/api/suppliers", "/api/storage-locations", "/api/color-catalog"])
async def test_reads_open_to_viewer(viewer_client: httpx.AsyncClient, path: str):
    assert (await viewer_client.get(path)).status_code == 200
