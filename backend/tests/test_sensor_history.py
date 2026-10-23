"""Q8b: sensor sample record/prune tasks, downsampled history routes, ACL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PrinterKind
from app.models.printing import Printer
from app.models.sensors import PrinterSensorSample
from app.tasks.sensors import prune, record

pytestmark = pytest.mark.usefixtures("library_root", "printer_enabled")


async def _printer(db: AsyncSession, serial: str = "S1") -> Printer:
    p = Printer(
        name=f"p-{serial}",
        kind=PrinterKind.BAMBU_LAN,
        host="h",
        serial=serial,
        access_code_enc="x",
        enabled=True,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _snap(ts: datetime, **kw) -> dict:
    return {"ts": ts.isoformat(), "nozzle": 200.0, "bed": 60.0, **kw}


async def test_record_persists_and_skips_unknown_printer(db_session: AsyncSession) -> None:
    p = await _printer(db_session)
    snap = _snap(
        datetime.now(UTC),
        nozzle_target=210.0,
        chamber=30.0,
        fan_part=50,
        ams=[{"id": 0, "humidity": 4, "temp": 25.0, "extra": "dropped"}],
    )
    assert record(p.id, snap) is not None
    assert record(p.id + 999, snap) is None
    row = (await db_session.execute(select(PrinterSensorSample))).scalar_one()
    assert (row.nozzle, row.nozzle_target, row.fan_part) == (200.0, 210.0, 50)
    assert row.ams == [{"id": 0, "humidity": 4, "temp": 25.0}]


async def test_prune_respects_sensor_history_days(db_session: AsyncSession) -> None:
    p = await _printer(db_session)
    now = datetime.now(UTC)
    record(p.id, _snap(now - timedelta(days=40)))
    record(p.id, _snap(now - timedelta(days=5)))
    assert prune() == 1
    left = (
        await db_session.execute(select(func.count()).select_from(PrinterSensorSample))
    ).scalar()
    assert left == 1


async def test_sensors_downsampled_to_max_points(
    authenticated_client, db_session: AsyncSession
) -> None:
    p = await _printer(db_session)
    now = datetime.now(UTC)
    db_session.add_all(
        PrinterSensorSample(printer_id=p.id, ts=now - timedelta(seconds=30 * i), nozzle=float(i))
        for i in range(1400)
    )
    await db_session.commit()
    r = await authenticated_client.get(
        f"/api/printers/{p.id}/sensors",
        params={"from": (now - timedelta(hours=12)).isoformat(), "to": now.isoformat()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 100 < len(body["points"]) <= 500
    assert body["step_s"] >= 87  # 12h / 500 points
    ts = [pt["ts"] for pt in body["points"]]
    assert ts == sorted(ts)
    r = await authenticated_client.get(
        f"/api/printers/{p.id}/sensors", params={"from": now.isoformat(), "to": now.isoformat()}
    )
    assert r.status_code == 422


async def test_ams_history_unit(authenticated_client, db_session: AsyncSession) -> None:
    p = await _printer(db_session)
    now = datetime.now(UTC)
    for i in range(3):
        db_session.add(
            PrinterSensorSample(
                printer_id=p.id,
                ts=now - timedelta(minutes=i * 10),
                ams=[
                    {"id": 0, "humidity": 3 + i, "temp": 24.0},
                    {"id": 1, "humidity": 9, "temp": 20.0},
                ],
            )
        )
    await db_session.commit()
    r = await authenticated_client.get(f"/api/printers/{p.id}/ams-history", params={"unit": 1})
    assert r.status_code == 200
    pts = r.json()["points"]
    assert pts and all(pt["humidity"] == 9 for pt in pts)
    r = await authenticated_client.get(f"/api/printers/{p.id}/ams-history", params={"unit": 5})
    assert r.json()["points"] == []


async def test_sensor_routes_require_print_access(
    make_user, make_group, login_as, db_session: AsyncSession
) -> None:
    a = await _printer(db_session, "A")
    b = await _printer(db_session, "B")
    allowed = await make_user("viewer")
    await make_group(
        "g",
        permissions=["printers:print"],
        members=[allowed],
        printer_access={a.id: (True, False, False)},
    )
    nothing = await make_user("viewer")
    ok = await login_as(allowed)
    for path in ("sensors", "ams-history"):
        assert (await ok.get(f"/api/printers/{a.id}/{path}")).status_code == 200
        assert (await ok.get(f"/api/printers/{b.id}/{path}")).status_code == 403
    no = await login_as(nothing)
    assert (await no.get(f"/api/printers/{a.id}/sensors")).status_code == 403
