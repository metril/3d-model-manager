"""Q7 maintenance: default task sets, due maths, routes, maintenance mode."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models import MaintenanceTask
from app.services import maintenance as svc

pytestmark = pytest.mark.usefixtures("printer_enabled")

CREATE = {"name": "X1C", "host": "10.0.0.5", "serial": "00M09A", "access_code": "12345678"}


def test_default_tasks_depend_on_model():
    x1 = {t[0] for t in svc.default_tasks("X1C")}
    a1 = {t[0] for t in svc.default_tasks("A1 mini")}
    assert "Clean carbon rods" in x1 and "Clean lidar & bed sensors" in x1
    assert "Clean carbon rods" not in a1
    assert "Clean build plate" in a1


def test_due_status_hours_and_days():
    now = datetime.now(UTC)
    t = MaintenanceTask(
        interval_hours=100, interval_days=None, last_done_hours=10.0, created_at=now
    )
    assert svc.due_status(t, 50.0, now).status == "ok"
    assert svc.due_status(t, 100.0, now).status == "soon"  # 90h / 100h
    d = svc.due_status(t, 110.0, now)
    assert d.status == "overdue" and d.hours_remaining == 0.0
    by_days = MaintenanceTask(
        interval_hours=None, interval_days=30, created_at=now - timedelta(days=31)
    )
    assert svc.due_status(by_days, 0, now).status == "overdue"
    never = MaintenanceTask(interval_hours=None, interval_days=None, created_at=now)
    assert svc.due_status(never, 999, now).status == "ok"


async def test_seed_on_create_and_mark_done(authenticated_client):
    c = authenticated_client
    pid = (await c.post("/api/printers", json={**CREATE, "model": "X1C"})).json()["id"]
    tasks = (await c.get(f"/api/printers/{pid}/maintenance")).json()
    assert any(t["name"] == "Clean carbon rods" for t in tasks)
    assert all(t["status"] == "ok" for t in tasks)

    tid = tasks[0]["id"]
    r = await c.post(f"/api/printers/{pid}/maintenance/{tid}/done", json={"note": "did it"})
    assert r.status_code == 200 and r.json()["last_done_at"] is not None
    logs = (await c.get(f"/api/printers/{pid}/maintenance/{tid}/logs")).json()
    assert len(logs) == 1 and logs[0]["note"] == "did it" and logs[0]["hours_at"] == 0.0

    # Custom task crud
    r = await c.post(f"/api/printers/{pid}/maintenance", json={"name": "Oil", "interval_days": 1})
    assert r.status_code == 201
    new_id = r.json()["id"]
    r = await c.patch(f"/api/printers/{pid}/maintenance/{new_id}", json={"notes": "n"})
    assert r.json()["notes"] == "n"
    assert (await c.delete(f"/api/printers/{pid}/maintenance/{new_id}")).status_code == 204
    assert (await c.delete(f"/api/printers/{pid}/maintenance/{new_id}")).status_code == 404
    # Wrong printer for a task id -> 404
    other = (await c.post("/api/printers", json={**CREATE, "serial": "00M09B"})).json()["id"]
    assert (await c.delete(f"/api/printers/{other}/maintenance/{tid}")).status_code == 404


async def test_maintenance_mode_roundtrip(authenticated_client):
    c = authenticated_client
    pid = (await c.post("/api/printers", json=CREATE)).json()["id"]
    r = await c.put(f"/api/printers/{pid}/maintenance/mode", json={"enabled": True})
    assert r.json() == {"maintenance_mode": True}
    assert (await c.get(f"/api/printers/{pid}")).json()["maintenance_mode"] is True


async def test_viewer_cannot_mutate(authenticated_client, viewer_client):
    pid = (await authenticated_client.post("/api/printers", json=CREATE)).json()["id"]
    r = await viewer_client.put(f"/api/printers/{pid}/maintenance/mode", json={"enabled": True})
    assert r.status_code == 403


async def test_seed_sets_baseline_not_overdue(authenticated_client, db_session):
    from sqlalchemy import select

    c = authenticated_client
    pid = (await c.post("/api/printers", json=CREATE)).json()["id"]
    rows = (
        await db_session.scalars(select(MaintenanceTask).where(MaintenanceTask.printer_id == pid))
    ).all()
    assert rows and all(t.last_done_hours == 0.0 for t in rows)
