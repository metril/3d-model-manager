"""Direct sends honour the plate wait + failed-print hold (queue parity)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Printer, PrintJob
from tests.test_print_plan_api import (  # noqa: F401  (fixtures + helpers)
    _clear_state,
    _printer,
    _seed_state,
    _sliced_file,
)

pytestmark = pytest.mark.usefixtures("library_root", "printer_enabled")

IDLE = {"gcode_state": "FINISH"}


async def _setup(db: AsyncSession, seed_file, redis_url, *, awaiting=False, failed=False):
    f, _m = await _sliced_file(db, seed_file)
    p = await _printer(db)
    if awaiting or failed:
        row = await db.get(Printer, p.id)
        row.awaiting_plate_clear = awaiting
        if failed:
            row.failed_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.commit()
    _seed_state(redis_url, p.id, IDLE)
    return f, p


async def _gate(client, on: bool):
    r = await client.put("/api/settings/queue", json={"require_plate_clear": on})
    assert r.status_code == 200, r.text


def _routes(f, p):
    body = {"file_id": f.id, "plate": 1}
    return {
        "send": ("/api/print/send", {**body, "printer_id": p.id}),
        "legacy": (f"/api/printers/{p.id}/print", body),
    }


@pytest.mark.parametrize("route", ["send", "legacy"])
@pytest.mark.parametrize(
    ("kind", "code"), [("plate", "plate_not_cleared"), ("failed", "printer_failed")]
)
async def test_hold_refuses_with_code(
    authenticated_client: httpx.AsyncClient,
    db_session,
    seed_file,
    redis_url,
    fake_adapter,
    route,
    kind,
    code,
):
    await _gate(authenticated_client, True)
    f, p = await _setup(
        db_session, seed_file, redis_url, awaiting=kind == "plate", failed=kind == "failed"
    )
    try:
        url, body = _routes(f, p)[route]
        r = await authenticated_client.post(url, json=body)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == code
        assert not fake_adapter.uploaded
    finally:
        _clear_state(redis_url, p.id)


async def test_gate_off_plate_ok_but_failed_still_refuses(
    authenticated_client, db_session, seed_file, redis_url, fake_adapter
):
    await _gate(authenticated_client, False)
    f, p = await _setup(db_session, seed_file, redis_url, awaiting=True)
    try:
        url, body = _routes(f, p)["send"]
        r = await authenticated_client.post(url, json=body)
        assert r.status_code == 201, r.text
    finally:
        _clear_state(redis_url, p.id)


async def test_gate_off_failed_refuses(
    authenticated_client, db_session, seed_file, redis_url, fake_adapter
):
    await _gate(authenticated_client, False)
    f, p = await _setup(db_session, seed_file, redis_url, failed=True)
    try:
        url, body = _routes(f, p)["send"]
        r = await authenticated_client.post(url, json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "printer_failed"
    finally:
        _clear_state(redis_url, p.id)


@pytest.mark.parametrize("route", ["send", "legacy"])
async def test_plate_cleared_acks_audits_and_sends(
    authenticated_client, db_session, seed_file, redis_url, fake_adapter, route
):
    await _gate(authenticated_client, True)
    f, p = await _setup(db_session, seed_file, redis_url, awaiting=True, failed=True)
    try:
        url, body = _routes(f, p)[route]
        r = await authenticated_client.post(url, json={**body, "plate_cleared": True})
        assert r.status_code == 201, r.text
        assert len(fake_adapter.uploaded) == 1
        await db_session.rollback()
        row = await db_session.get(Printer, p.id, populate_existing=True)
        assert row.awaiting_plate_clear is False and row.plate_cleared_at is not None
        acts = (
            await db_session.scalars(select(AuditLog).where(AuditLog.action == "clear_plate"))
        ).all()
        assert len(acts) == 1
        assert len((await db_session.scalars(select(PrintJob))).all()) == 1
    finally:
        _clear_state(redis_url, p.id)


async def test_plate_cleared_needs_control(
    authenticated_client,
    member_client,
    member,
    make_group,
    db_session,
    seed_file,
    redis_url,
    fake_adapter,
):
    await _gate(authenticated_client, True)
    f, p = await _setup(db_session, seed_file, redis_url, awaiting=True)
    await make_group("g", members=[member], printer_access={p.id: (True, False, False)})
    try:
        body = {"file_id": f.id, "printer_id": p.id}
        r = await member_client.post("/api/print/send", json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "plate_not_cleared"
        r = await member_client.post("/api/print/send", json={**body, "plate_cleared": True})
        assert r.status_code == 403
        assert not fake_adapter.uploaded
    finally:
        _clear_state(redis_url, p.id)


async def test_no_hold_sends_normally(
    authenticated_client, db_session, seed_file, redis_url, fake_adapter
):
    await _gate(authenticated_client, True)
    f, p = await _setup(db_session, seed_file, redis_url)
    try:
        url, body = _routes(f, p)["send"]
        r = await authenticated_client.post(url, json=body)
        assert r.status_code == 201, r.text
        assert (await db_session.scalars(select(AuditLog))).all() == []
    finally:
        _clear_state(redis_url, p.id)


async def test_plan_reports_hold(authenticated_client, db_session, seed_file, redis_url):
    await _gate(authenticated_client, True)
    f, p = await _setup(db_session, seed_file, redis_url, awaiting=True)
    try:
        r = await authenticated_client.post(
            "/api/print/plan", json={"file_id": f.id, "printer_id": p.id}
        )
        assert r.status_code == 200, r.text
        assert r.json()["hold"] == "plate_not_cleared"
    finally:
        _clear_state(redis_url, p.id)
