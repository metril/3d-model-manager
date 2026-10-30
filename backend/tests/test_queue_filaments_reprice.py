"""Queue-card filament details, cost estimate, and run repricing."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Printer, PrintJob, Spool, SpoolUsage
from app.models.enums import PrinterKind, PrintJobOrigin, PrintJobState, SpoolUsageSource
from app.models.library import Print
from app.services.costing import CostRates
from app.services.queue_filaments import build_details, colour_differs

RATES = CostRates(filament_per_kg=20.0, machine_per_hour=1.0)
PLATE = {
    "index": 1,
    "prediction_s": 3600,
    "weight_g": 30,
    "filaments": [
        {"filament_id": 1, "type": "PLA", "color": "#FF0000", "used_g": 10},
        {"filament_id": 2, "type": "PLA", "color": "#00FF00", "used_g": 20},
    ],
}


def _spool(color: str, per_kg: float | None, sid: int | None = None) -> Spool:
    return Spool(
        id=sid,
        material_type="PLA",
        brand="Acme",
        color_hex=color,
        cost_per_kg=per_kg,
        label_weight_g=1000,
        weight_used_g=0,
    )


def test_colour_tolerance() -> None:
    assert not colour_differs("#FF0000", "#FF0101")
    assert colour_differs("#FF0000", "#0000FF")
    assert not colour_differs(None, "#0000FF")


def test_details_shape_mismatch_and_estimate() -> None:
    spools = {0: _spool("#FF0000", 50.0, 1), 1: _spool("#0000FF", None, 2)}
    d = build_details(PLATE, [0, 1], spools, copies=2, rates=RATES, wear_per_hour=0.5)
    assert [f.spool_id for f in d.filaments] == [1, 2]
    assert [f.colour_mismatch for f in d.filaments] == [False, True]
    assert d.colour_mismatch
    # per copy: 10g@50 + 20g@global20 = 0.5 + 0.4; machine 1.0; wear 0.5
    assert d.estimated_cost == pytest.approx((0.9 + 1.0 + 0.5) * 2)


def test_details_unmapped_has_no_spool() -> None:
    d = build_details(PLATE, [-1, -1], {}, copies=1, rates=RATES, wear_per_hour=None)
    assert all(f.spool_id is None and not f.colour_mismatch for f in d.filaments)
    assert d.estimated_cost == pytest.approx(0.6 + 1.0)


async def test_reprice_changes_only_filament_cost(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    p = Printer(name="p", kind=PrinterKind.BAMBU_LAN, host="h", serial="s", access_code_enc="x")
    db_session.add(p)
    await db_session.flush()
    job = PrintJob(
        printer_id=p.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="j",
        state=PrintJobState.FINISHED,
    )
    spool = _spool("#FF0000", 100.0)
    db_session.add_all([job, spool])
    await db_session.flush()
    db_session.add(
        SpoolUsage(
            spool_id=spool.id, print_job_id=job.id, grams=100, source=SpoolUsageSource.THREE_MF
        )
    )
    run = Print(
        print_job_id=job.id,
        filament_g=100,
        filament_cost=2.0,
        machine_cost=1.0,
        wear_cost=0.5,
        energy_cost=0.25,
        total_cost=3.75,
    )
    db_session.add(run)
    await db_session.commit()

    r = await authenticated_client.post("/api/archives/reprice", json={"dry_run": True})
    assert r.status_code == 200
    assert r.json()["repriced"] == 1 and r.json()["delta"] == pytest.approx(8.0)
    await db_session.refresh(run)
    assert run.filament_cost == 2.0

    r = await authenticated_client.post("/api/archives/reprice", json={})
    assert r.json()["repriced"] == 1
    await db_session.refresh(run)
    assert run.filament_cost == pytest.approx(10.0)
    assert (run.machine_cost, run.wear_cost, run.energy_cost) == (1.0, 0.5, 0.25)
    assert run.total_cost == pytest.approx(11.75)
    r = await authenticated_client.post("/api/archives/reprice", json={})
    assert r.json()["repriced"] == 0
