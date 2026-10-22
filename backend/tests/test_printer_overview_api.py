"""``GET /api/printers/overview`` (P3 Task 1): per-user dashboard payload --
every visible printer's live status + active job + recent history, plus the
queue's head and cross-printer recent runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_engine
from app.models import Model, Printer, PrintJob
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
)
from app.models.printing import PrintArchive
from app.printers.base import state_key

pytestmark = pytest.mark.usefixtures("library_root")


async def _printer(db: AsyncSession, name: str) -> Printer:
    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.9", serial=name, access_code_enc="x"
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _finished_archive(
    db: AsyncSession, printer: Printer, *, n: int, model: Model | None = None
) -> PrintArchive:
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name=f"job{n}",
        state=PrintJobState.FINISHED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=n),
    )
    db.add(job)
    await db.flush()
    a = PrintArchive(
        print_job_id=job.id,
        model_id=model.id if model else None,
        result=ArchiveResult.COMPLETED,
        duration_s=3600,
        fetch_state=ArchiveFetchState.DONE,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


async def _active_job(db: AsyncSession, printer: Printer) -> PrintJob:
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.APP,
        state=PrintJobState.PRINTING,
        layer=10,
        total_layers=100,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def test_overview_shape(
    authenticated_client: httpx.AsyncClient,
    printer_enabled: None,
    redis_client: aioredis.Redis,
    db_session: AsyncSession,
):
    p1 = await _printer(db_session, "p1")
    p2 = await _printer(db_session, "p2")
    await _active_job(db_session, p1)
    await redis_client.set(
        state_key(p1.id), json.dumps({"gcode_state": "RUNNING", "layer_num": 10})
    )
    for n in range(7):
        await _finished_archive(db_session, p1, n=n)

    body = (await authenticated_client.get("/api/printers/overview")).json()
    assert body.keys() == {"printers", "up_next", "recent_runs"}
    items = {item["printer"]["id"]: item for item in body["printers"]}
    assert set(items) == {p1.id, p2.id}
    assert items[p1.id]["status"]["online"] is True
    assert items[p1.id]["active_job"]["layer"] == 10
    assert items[p2.id]["active_job"] is None
    assert items[p2.id]["status"]["online"] is False
    assert len(items[p1.id]["last_runs"]) <= 5
    assert len(body["up_next"]) <= 3


async def test_overview_hides_invisible_printers(
    make_user,
    make_group,
    login_as,
    printer_enabled: None,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
):
    p1 = await _printer(db_session, "p1")
    p2 = await _printer(db_session, "p2")
    await _finished_archive(db_session, p1, n=1)
    await _finished_archive(db_session, p2, n=2)

    user = await make_user("member")
    await make_group("printer-folks", members=[user], printer_access={p1.id: (True, False, False)})
    client = await login_as(user)

    body = (await client.get("/api/printers/overview")).json()
    assert [item["printer"]["id"] for item in body["printers"]] == [p1.id]
    assert {a["printer_id"] for a in body["recent_runs"]}.issubset({p1.id})
    assert body["printers"][0]["can_print"] is True
    assert body["printers"][0]["can_control"] is False


async def _count_statements(engine, coro) -> int:
    count = 0

    def _tick(*_args: object, **_kwargs: object) -> None:
        nonlocal count
        count += 1

    sync_engine = engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _tick)
    try:
        await coro
    finally:
        event.remove(sync_engine, "before_cursor_execute", _tick)
    return count


async def test_overview_constant_query_count(
    authenticated_client: httpx.AsyncClient,
    printer_enabled: None,
    db_session: AsyncSession,
):
    p1 = await _printer(db_session, "only")
    await _finished_archive(db_session, p1, n=1)

    engine = get_engine()
    one_count = await _count_statements(engine, authenticated_client.get("/api/printers/overview"))

    for i in range(2):
        other = await _printer(db_session, f"extra{i}")
        await _finished_archive(db_session, other, n=i)

    three_count = await _count_statements(
        engine, authenticated_client.get("/api/printers/overview")
    )
    assert three_count == one_count


async def test_overview_503_when_disabled(authenticated_client: httpx.AsyncClient):
    assert (await authenticated_client.get("/api/printers/overview")).status_code == 503
