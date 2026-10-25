"""T4: queue scheduling (scheduled/manual exclusion, previous-success skip,
preheat/soak, keep-warm, auto power-off) and print batches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import redis as redis_lib
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, Setting
from app.models.enums import ArchiveFetchState, ArchiveResult, PrintJobState, QueueState
from app.models.library import PrintBatch, PrintQueueEntry
from app.models.printing import PrintArchive, Printer, PrintJob
from app.printers.capabilities import capabilities_for
from app.services import dispatcher, queue_schedule
from app.services.notify import events
from app.services.queue_preheat import QueuePreheatConfig
from app.tasks import base
from tests.test_dispatcher import (
    IDLE_STATE,
    _seed_admin,
    _seed_model_file,
    _seed_printer,
    _seed_state,
)
from tests.test_dispatcher import _seed_entry as _base_seed_entry

pytestmark = pytest.mark.usefixtures("data_dir", "printer_enabled", "library_root")


@pytest.fixture(autouse=True)
def _clean_redis(redis_url: str):
    """Entry/printer ids restart per test (table truncate), so stale
    ``queue:*`` Redis state would alias across tests."""
    c = redis_lib.Redis.from_url(redis_url)
    c.flushdb()
    c.close()


def _seed_entry(session, **kw):
    kw.setdefault("created_by", _seed_admin(session))
    return _base_seed_entry(session, **kw)


def _tick(redis_url: str) -> dict:
    client = redis_lib.Redis.from_url(redis_url)
    try:
        with base.sync_session() as s:
            return dispatcher.dispatch_once(s, client, get_settings())
    finally:
        client.close()


def _get(entry_id: int) -> PrintQueueEntry:
    with base.sync_session() as s:
        e = s.get(PrintQueueEntry, entry_id)
        s.expunge(e)
        return e


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    out: list[tuple] = []
    monkeypatch.setattr(
        events, "emit", lambda ev, pid=None, payload=None: out.append((ev, pid, payload))
    )
    return out


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    out: list[tuple] = []
    monkeypatch.setattr(
        queue_schedule, "send_command", lambda c, pid, cmd, args: out.append((pid, cmd, args))
    )
    return out


# ---------------------------------------------------------------------------
# eligibility: scheduled_at + manual_start
# ---------------------------------------------------------------------------


def test_scheduled_and_manual_exclusion():
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="sched-excl")
        future = _seed_entry(s, model=model, file=file, state=QueueState.READY)
        past = _seed_entry(s, model=model, file=file, state=QueueState.READY)
        manual = _seed_entry(s, model=model, file=file, state=QueueState.READY)
        released = _seed_entry(s, model=model, file=file, state=QueueState.READY)
        future.scheduled_at = datetime.now(UTC) + timedelta(hours=1)
        past.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
        manual.manual_start = True
        released.manual_start = True
        released.released_at = datetime.now(UTC)
        s.commit()
        ids = {"future": future.id, "past": past.id, "manual": manual.id, "released": released.id}

        eligible = {e.id for e in dispatcher.eligible_queue_items(s)}
        assert eligible == {ids["past"], ids["released"]}
        everything = {e.id for e in dispatcher.eligible_queue_items(s, include_deferred=True)}
        assert everything == set(ids.values())


def test_scheduled_entry_dispatches_once_due(redis_url: str, fake_adapter):
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="sched-due")
        printer = _seed_printer(s, name="SchedDueP")
        e = _seed_entry(s, model=model, file=file, state=QueueState.READY)
        e.scheduled_at = datetime.now(UTC) + timedelta(hours=1)
        s.commit()
        eid, pid = e.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)
    assert _tick(redis_url)["sent"] == 0
    with base.sync_session() as s:
        s.get(PrintQueueEntry, eid).scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    counts = _tick(redis_url)
    e = _get(eid)
    assert counts["sent"] == 1, (counts, e.state, e.last_error, e.hold_reason)
    assert e.state == QueueState.DISPATCHED


# ---------------------------------------------------------------------------
# previous-success matrix
# ---------------------------------------------------------------------------


def _prev(s, model, file, printer_id, *, state, job_state=None, archive=None, batch_id=None):
    e = _seed_entry(s, model=model, file=file, state=state)
    e.assigned_printer_id = printer_id
    e.batch_id = batch_id
    if job_state is not None:
        job = PrintJob(
            printer_id=printer_id,
            file_id=file.id,
            state=job_state,
            started_at=datetime.now(UTC),
        )
        s.add(job)
        s.flush()
        e.print_job_id = job.id
        if archive is not None:
            s.add(
                PrintArchive(
                    print_job_id=job.id,
                    result=archive,
                    fetch_state=ArchiveFetchState.PENDING
                    if hasattr(ArchiveFetchState, "PENDING")
                    else list(ArchiveFetchState)[0],
                )
            )
    s.commit()
    return e


def _scenario(redis_url, slug, build, *, batch_for_current=False):
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug=slug)
        printer = _seed_printer(s, name=f"P-{slug}")
        batch_id = None
        if batch_for_current:
            b = PrintBatch(name="b", quantity=1)
            s.add(b)
            s.commit()
            batch_id = b.id
        build(s, model, file, printer.id)
        cur = _seed_entry(s, model=model, file=file, state=QueueState.READY)
        cur.require_previous_success = True
        cur.batch_id = batch_id
        s.commit()
        cid, pid = cur.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)
    return cid


def test_previous_failed_skips_and_notifies(redis_url: str, fake_adapter, emitted):
    cid = _scenario(
        redis_url,
        "prev-failed",
        lambda s, m, f, pid: _prev(
            s, m, f, pid, state=QueueState.FAILED, job_state=PrintJobState.FAILED
        ),
    )
    counts = _tick(redis_url)
    e = _get(cid)
    assert counts["skipped"] == 1 and counts["sent"] == 0
    assert e.state == QueueState.SKIPPED and e.hold_reason == "previous_failed"
    assert [x[0] for x in emitted] == ["queue_item_skipped"]
    assert emitted[0][2]["queue_id"] == cid


@pytest.mark.parametrize("result", [ArchiveResult.FAILED, ArchiveResult.CANCELED])
def test_previous_done_with_bad_archive_skips(redis_url: str, fake_adapter, emitted, result):
    cid = _scenario(
        redis_url,
        f"prev-arch-{result.value}",
        lambda s, m, f, pid: _prev(
            s, m, f, pid, state=QueueState.DONE, job_state=PrintJobState.FINISHED, archive=result
        ),
    )
    _tick(redis_url)
    assert _get(cid).state == QueueState.SKIPPED


def test_previous_done_completed_runs(redis_url: str, fake_adapter, emitted):
    cid = _scenario(
        redis_url,
        "prev-ok",
        lambda s, m, f, pid: _prev(
            s,
            m,
            f,
            pid,
            state=QueueState.DONE,
            job_state=PrintJobState.FINISHED,
            archive=ArchiveResult.COMPLETED,
        ),
    )
    _tick(redis_url)
    assert _get(cid).state == QueueState.DISPATCHED and not emitted


def test_previous_cancelled_is_neutral(redis_url: str, fake_adapter, emitted):
    def build(s, m, f, pid):
        _prev(
            s,
            m,
            f,
            pid,
            state=QueueState.DONE,
            job_state=PrintJobState.FINISHED,
            archive=ArchiveResult.COMPLETED,
        )
        _prev(s, m, f, pid, state=QueueState.FAILED, job_state=PrintJobState.CANCELED)

    cid = _scenario(redis_url, "prev-cancel", build)
    _tick(redis_url)
    assert _get(cid).state == QueueState.DISPATCHED


def test_no_previous_runs(redis_url: str, fake_adapter, emitted):
    cid = _scenario(redis_url, "prev-none", lambda *a: None)
    _tick(redis_url)
    assert _get(cid).state == QueueState.DISPATCHED


def test_previous_check_is_batch_scoped(redis_url: str, fake_adapter, emitted):
    # the failure belongs to no batch; the current entry is in a batch -> ignored
    cid = _scenario(
        redis_url,
        "prev-batch",
        lambda s, m, f, pid: _prev(
            s, m, f, pid, state=QueueState.FAILED, job_state=PrintJobState.FAILED
        ),
        batch_for_current=True,
    )
    _tick(redis_url)
    assert _get(cid).state == QueueState.DISPATCHED


def test_flag_off_ignores_failure(redis_url: str, fake_adapter, emitted):
    cid = _scenario(
        redis_url,
        "prev-flag-off",
        lambda s, m, f, pid: _prev(
            s, m, f, pid, state=QueueState.FAILED, job_state=PrintJobState.FAILED
        ),
    )
    with base.sync_session() as s:
        s.get(PrintQueueEntry, cid).require_previous_success = False
        s.commit()
    _tick(redis_url)
    assert _get(cid).state == QueueState.DISPATCHED


# ---------------------------------------------------------------------------
# preheat / soak state machine
# ---------------------------------------------------------------------------


class _Clock:
    t = 1000.0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    c.t = 1000.0
    monkeypatch.setattr(queue_schedule, "_now", lambda: c.t)
    return c


def _gate_setup(redis_url, slug, *, model="A1 mini", **entry_kw):
    with base.sync_session() as s:
        m, _, f = _seed_model_file(s, slug=slug)
        printer = _seed_printer(s, name=f"P-{slug}", model=model)
        e = _seed_entry(s, model=m, file=f, state=QueueState.READY)
        for k, v in entry_kw.items():
            setattr(e, k, v)
        s.commit()
        return e.id, printer.id


def _gate(redis_url, eid, pid, cfg, types=("PLA",)):
    client = redis_lib.Redis.from_url(redis_url)
    try:
        with base.sync_session() as s:
            ok = queue_schedule.preheat_gate(
                s, client, cfg, s.get(PrintQueueEntry, eid), s.get(Printer, pid), list(types)
            )
            return ok, s.get(PrintQueueEntry, eid).stage
    finally:
        client.close()


def test_preheat_bed_path_reaches_target(redis_url: str, clock, sent):
    eid, pid = _gate_setup(redis_url, "ph-bed")
    cfg = QueuePreheatConfig(enabled=True, bed_targets={"pla": 60}, soak_s=0)
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 30})
    assert _gate(redis_url, eid, pid, cfg) == (False, "preheating")
    assert sent == [(pid, "set_temperature", {"target": "bed", "value": 60})]
    clock.t += 20
    assert _gate(redis_url, eid, pid, cfg) == (False, "preheating")
    assert len(sent) == 1  # sent once
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 58.5})  # within 2 C
    assert _gate(redis_url, eid, pid, cfg) == (True, None)


def test_preheat_chamber_path_and_explicit_target(redis_url: str, clock, sent):
    assert capabilities_for("X1E").chamber_heater
    eid, pid = _gate_setup(redis_url, "ph-chamber", model="X1E")
    cfg = QueuePreheatConfig(enabled=True, chamber_targets={"ABS": 45, "ASA": 50})
    _seed_state(redis_url, pid, {**IDLE_STATE, "chamber_temper": 20})
    assert _gate(redis_url, eid, pid, cfg, types=("ABS", "ASA"))[0] is False
    assert sent[0] == (
        pid,
        "set_temperature",
        {"target": "chamber", "value": 50},
    )  # max over filaments

    eid2, pid2 = _gate_setup(redis_url, "ph-chamber2", model="X1E", preheat_chamber_target=38)
    _gate(redis_url, eid2, pid2, cfg, types=("PLA",))
    assert sent[-1] == (pid2, "set_temperature", {"target": "chamber", "value": 38})


def test_preheat_max_wait_timeout(redis_url: str, clock, sent):
    eid, pid = _gate_setup(redis_url, "ph-timeout")
    cfg = QueuePreheatConfig(enabled=True, bed_targets={"PLA": 60}, max_wait_s=120)
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 25})
    assert _gate(redis_url, eid, pid, cfg)[0] is False
    clock.t += 119
    assert _gate(redis_url, eid, pid, cfg)[0] is False
    clock.t += 2
    assert _gate(redis_url, eid, pid, cfg) == (True, None)


def test_preheat_soak_timing(redis_url: str, clock, sent):
    eid, pid = _gate_setup(redis_url, "ph-soak")
    cfg = QueuePreheatConfig(enabled=True, bed_targets={"PLA": 60}, soak_s=60)
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 60})
    assert _gate(redis_url, eid, pid, cfg) == (False, "preheating")
    assert _gate(redis_url, eid, pid, cfg) == (False, "soaking")
    clock.t += 30
    assert _gate(redis_url, eid, pid, cfg) == (False, "soaking")
    clock.t += 31
    assert _gate(redis_url, eid, pid, cfg) == (True, None)


def test_preheat_override(redis_url: str, clock, sent):
    cfg_on = QueuePreheatConfig(enabled=True, bed_targets={"PLA": 60})
    eid, pid = _gate_setup(redis_url, "ph-off", preheat_override="off")
    assert _gate(redis_url, eid, pid, cfg_on) == (True, None) and not sent

    cfg_off = QueuePreheatConfig(enabled=False, bed_targets={"PLA": 60})
    eid, pid = _gate_setup(redis_url, "ph-on", preheat_override="on")
    assert _gate(redis_url, eid, pid, cfg_off)[0] is False and sent

    # enabled but no target for this filament -> nothing to do
    eid, pid = _gate_setup(redis_url, "ph-notarget")
    assert _gate(redis_url, eid, pid, QueuePreheatConfig(enabled=True))[0] is True


def test_preheat_holds_dispatch_end_to_end(redis_url: str, fake_adapter, clock, sent):
    eid, pid = _gate_setup(redis_url, "ph-e2e")
    with base.sync_session() as s:
        s.add(
            Setting(
                key="queue_preheat",
                value=QueuePreheatConfig(enabled=True, bed_targets={"PLA": 60}).model_dump(),
            )
        )
        s.commit()
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 30})
    assert _tick(redis_url)["sent"] == 0
    assert _get(eid).stage == "preheating" and _get(eid).state == QueueState.READY
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 60})
    assert _tick(redis_url)["sent"] == 1
    e = _get(eid)
    assert e.state == QueueState.DISPATCHED and e.stage is None


# ---------------------------------------------------------------------------
# keep-warm
# ---------------------------------------------------------------------------


def test_keep_warm_send_and_cancel(redis_url: str, clock, sent):
    with base.sync_session() as s:
        pid = _seed_printer(s, name="KeepWarmP").id
    client = redis_lib.Redis.from_url(redis_url)
    cfg = QueuePreheatConfig(keep_warm_enabled=True, keep_warm_bed_c=50, keep_warm_max_min=10)
    _seed_state(redis_url, pid, {**IDLE_STATE, "gcode_state": "FINISH"})

    def run(pending):
        with base.sync_session() as s:
            queue_schedule.run_keep_warm(s, client, cfg, pending)

    run(set())
    assert sent == []  # nothing queued -> no warm
    run({pid})
    assert sent == [(pid, "set_temperature", {"target": "bed", "value": 50})]
    run({pid})
    assert len(sent) == 1
    run(set())  # queue emptied -> release
    assert sent[-1] == (pid, "set_temperature", {"target": "bed", "value": 0})
    run(set())
    assert len(sent) == 2

    # window expiry
    client.delete(queue_schedule.keepwarm_key(pid))
    run({pid})
    clock.t += 11 * 60
    run({pid})
    assert sent[-1][2]["value"] == 0 and len(sent) == 4
    client.close()


# ---------------------------------------------------------------------------
# auto power-off
# ---------------------------------------------------------------------------


def _auto_off_setup(redis_url, slug, *, plug=True, other_queued=False, hot=False):
    from tests.test_dispatcher import _rest_plug

    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug=slug)
        printer = _seed_printer(s, name=f"P-{slug}")
        job = PrintJob(printer_id=printer.id, file_id=file.id, state=PrintJobState.FINISHED)
        s.add(job)
        s.flush()
        e = _seed_entry(s, model=model, file=file, state=QueueState.DISPATCHED)
        e.print_job_id, e.assigned_printer_id, e.auto_off_after = job.id, printer.id, True
        if other_queued:
            o = _seed_entry(
                s, model=model, file=file, state=QueueState.READY, printer_id=printer.id
            )
            o.manual_start = True  # deferred, but still queued work
        if plug:
            _rest_plug(s, printer.id)
        s.commit()
        pid = printer.id
    _seed_state(
        redis_url,
        pid,
        {**IDLE_STATE, "gcode_state": "FINISH", "nozzle_temper": 200.0 if hot else 25.0},
    )
    return pid


@pytest.fixture
def plug_off(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    from app.services.smart_plugs import manager

    calls: list[int] = []

    def fake(session, settings, plug, action):
        calls.append(plug.printer_id)
        plug.last_state = action
        session.commit()
        return plug

    monkeypatch.setattr(manager, "command", fake)
    return calls


def test_auto_off_after_print(redis_url: str, fake_adapter, plug_off):
    pid = _auto_off_setup(redis_url, "ao-ok")
    _tick(redis_url)
    assert plug_off == [pid]
    with base.sync_session() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "queue_auto_off")) is not None


def test_auto_off_waits_for_cold_nozzle(redis_url: str, fake_adapter, plug_off):
    pid = _auto_off_setup(redis_url, "ao-hot", hot=True)
    _tick(redis_url)
    assert plug_off == []
    _seed_state(redis_url, pid, {**IDLE_STATE, "gcode_state": "FINISH", "nozzle_temper": 30.0})
    _tick(redis_url)
    assert plug_off == [pid]


def test_auto_off_no_plug(redis_url: str, fake_adapter, plug_off):
    _auto_off_setup(redis_url, "ao-noplug", plug=False)
    _tick(redis_url)
    _tick(redis_url)
    assert plug_off == []


def test_auto_off_skipped_when_more_work_queued(redis_url: str, fake_adapter, plug_off):
    _auto_off_setup(redis_url, "ao-queued", other_queued=True)
    _tick(redis_url)
    assert plug_off == []


# ---------------------------------------------------------------------------
# API: PATCH / start / cancel-schedule / resume / perms
# ---------------------------------------------------------------------------


async def _make_reviewer(make_user, make_group, login_as):
    user = await make_user("member")
    await make_group("reviewers", permissions=["queue:review"], members=[user])
    return await login_as(user)


def _seed_api_entry(**kw) -> int:
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug=f"api-{datetime.now(UTC).timestamp()}")
        e = _seed_entry(s, model=model, file=file, state=kw.pop("state", QueueState.READY))
        for k, v in kw.items():
            setattr(e, k, v)
        s.commit()
        return e.id


async def test_patch_scheduling_fields(authenticated_client: httpx.AsyncClient):
    eid = _seed_api_entry()
    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    r = await authenticated_client.patch(
        f"/api/queue/{eid}",
        json={
            "scheduled_at": when,
            "manual_start": True,
            "require_previous_success": True,
            "auto_off_after": True,
            "preheat_override": "on",
            "preheat_chamber_target": 40,
        },
    )
    assert r.status_code == 200, r.text
    row = next(e for e in r.json() if e["id"] == eid)
    assert row["manual_start"] and row["require_previous_success"] and row["auto_off_after"]
    assert row["preheat_override"] == "on" and row["preheat_chamber_target"] == 40
    assert row["waiting_reason"] == "scheduled"

    r = await authenticated_client.patch(f"/api/queue/{eid}", json={"scheduled_at": None})
    row = next(e for e in r.json() if e["id"] == eid)
    assert row["scheduled_at"] is None and row["waiting_reason"] == "manual"

    assert (await authenticated_client.patch(f"/api/queue/{eid}", json={})).status_code == 422
    r = await authenticated_client.patch(f"/api/queue/{eid}", json={"position": 1})
    assert r.status_code == 200


async def test_start_release_flow(authenticated_client: httpx.AsyncClient):
    eid = _seed_api_entry(manual_start=True)
    r = await authenticated_client.post(f"/api/queue/{eid}/start")
    assert r.status_code == 200, r.text
    assert r.json()["released_at"] is not None and r.json()["waiting_reason"] is None
    with base.sync_session() as s:
        e = s.get(PrintQueueEntry, eid)
        assert e.released_by_user_id is not None
        assert e in dispatcher.eligible_queue_items(s)


async def test_cancel_schedule(authenticated_client: httpx.AsyncClient):
    eid = _seed_api_entry(scheduled_at=datetime.now(UTC) + timedelta(hours=3))
    r = await authenticated_client.post(f"/api/queue/{eid}/cancel-schedule")
    assert r.status_code == 200 and r.json()["scheduled_at"] is None


async def test_resume_requeues_skipped(authenticated_client: httpx.AsyncClient):
    with base.sync_session() as s:
        pid = _seed_printer(s, name="ResumeP").id
    skipped = _seed_api_entry(
        state=QueueState.SKIPPED,
        hold_reason="previous_failed",
        assigned_printer_id=pid,
        require_previous_success=True,
    )
    other = _seed_api_entry(state=QueueState.SKIPPED, hold_reason="previous_failed")
    listing = (await authenticated_client.get("/api/queue")).json()
    row = next(e for e in listing if e["id"] == skipped)
    assert row["skipped_reason"] == "previous_failed"
    r = await authenticated_client.post(f"/api/printers/{pid}/queue/resume")
    assert r.status_code == 200 and r.json() == {"resumed": 1}
    e, o = _get(skipped), _get(other)
    assert e.state == QueueState.READY and e.hold_reason is None
    assert e.require_previous_success is False  # acknowledged
    assert o.state == QueueState.SKIPPED
    with base.sync_session() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "queue_resume")) is not None
    assert (await authenticated_client.post("/api/printers/9999/queue/resume")).status_code == 404


async def test_schedule_routes_perms(
    viewer_client, make_user, make_group, login_as, authenticated_client
):
    eid = _seed_api_entry(manual_start=True)
    assert (await viewer_client.post(f"/api/queue/{eid}/start")).status_code == 403
    assert (await viewer_client.post("/api/printers/1/queue/resume")).status_code == 403
    assert (await viewer_client.get("/api/batches")).status_code == 403
    assert (
        await viewer_client.put("/api/settings/queue-preheat", json={"enabled": True})
    ).status_code == 403
    reviewer = await _make_reviewer(make_user, make_group, login_as)
    assert (await reviewer.post(f"/api/queue/{eid}/start")).status_code == 200
    assert (await reviewer.get("/api/batches")).status_code == 200
    # reviewer may not write settings
    assert (
        await reviewer.put("/api/settings/queue-preheat", json={"enabled": True})
    ).status_code == 403


async def test_preheat_settings_roundtrip(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.get("/api/settings/queue-preheat")
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["max_wait_s"] == 900
    body = {
        "enabled": True,
        "chamber_targets": {"abs": 45},
        "bed_targets": {"PETG": 70},
        "max_wait_s": 600,
        "soak_s": 120,
        "keep_warm_enabled": True,
        "keep_warm_bed_c": 50,
        "keep_warm_max_min": 60,
    }
    r = await authenticated_client.put("/api/settings/queue-preheat", json=body)
    assert r.status_code == 200 and r.json()["chamber_targets"] == {"ABS": 45}
    assert (await authenticated_client.get("/api/settings/queue-preheat")).json() == r.json()
    for bad in (
        {"max_wait_s": 59},
        {"soak_s": 1801},
        {"keep_warm_bed_c": 39},
        {"keep_warm_max_min": 4},
    ):
        assert (
            await authenticated_client.put("/api/settings/queue-preheat", json=bad)
        ).status_code == 422


# ---------------------------------------------------------------------------
# batches
# ---------------------------------------------------------------------------


def _file_id() -> int:
    with base.sync_session() as s:
        _, _, file = _seed_model_file(s, slug=f"batch-{datetime.now(UTC).timestamp()}")
        return file.id


async def _new_batch(client, **kw) -> dict:
    body = {"name": "run", "file_id": _file_id(), "quantity": 3, **kw}
    r = await client.post("/api/batches", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_batch_create_validation(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    assert (await c.post("/api/batches", json={"name": "x", "quantity": 1})).status_code == 422
    fid = _file_id()
    for q in (0, 501):
        assert (
            await c.post("/api/batches", json={"name": "x", "file_id": fid, "quantity": q})
        ).status_code == 422
    r = await c.post(
        "/api/batches",
        json={"name": "x", "file_id": fid, "quantity": 2, "plate_targets": {"1": 3}},
    )
    assert r.status_code == 422
    assert (
        await c.post("/api/batches", json={"name": "x", "file_id": 99999, "quantity": 1})
    ).status_code == 404


async def test_batch_dispatch_plates_progress_and_status(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    b = await _new_batch(
        c,
        quantity=4,
        plate_targets={"2": 1, "1": 2},
        scheduled_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        manual_start=True,
        auto_off_after=True,
    )
    assert b["status"] == "draft" and b["progress"]["total"] == 0
    r = await c.post(f"/api/batches/{b['id']}/dispatch")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active" and r.json()["progress"] == {
        "total": 4,
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "waiting": 4,
    }
    assert (await c.post(f"/api/batches/{b['id']}/dispatch")).status_code == 409

    entries = (await c.get(f"/api/queue?batch_id={b['id']}")).json()
    assert len(entries) == 4
    assert sorted(e["plate"] or 0 for e in entries) == [0, 1, 1, 2]
    assert all(
        e["copies"] == 1
        and e["manual_start"]
        and e["auto_off_after"]
        and e["batch"]["id"] == b["id"]
        for e in entries
    )
    assert all(e["scheduled_at"] is not None for e in entries)
    assert (await c.get("/api/queue?batch_id=999999")).json() == []

    # mixed terminal states -> progress; all terminal -> done
    states = [QueueState.DONE, QueueState.DONE, QueueState.FAILED, QueueState.SKIPPED]
    with base.sync_session() as s:
        for e, st in zip(entries[:3], states[:3], strict=True):
            s.get(PrintQueueEntry, e["id"]).state = st
        s.commit()
    cur = (await c.get(f"/api/batches/{b['id']}")).json()
    assert cur["status"] == "active" and cur["progress"]["waiting"] == 1
    with base.sync_session() as s:
        s.get(PrintQueueEntry, entries[3]["id"]).state = QueueState.SKIPPED
        s.commit()
    cur = (await c.get(f"/api/batches/{b['id']}")).json()
    assert cur["status"] == "done"
    assert cur["progress"] == {"total": 4, "done": 2, "failed": 1, "skipped": 1, "waiting": 0}
    assert any(x["id"] == b["id"] for x in (await c.get("/api/batches")).json())
    with base.sync_session() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "batch_dispatch")) is not None


async def test_batch_ungroup_and_patch(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    b = await _new_batch(c, quantity=2)
    r = await c.patch(f"/api/batches/{b['id']}", json={"name": "renamed", "quantity": 3})
    assert r.status_code == 200 and r.json()["name"] == "renamed" and r.json()["quantity"] == 3
    await c.post(f"/api/batches/{b['id']}/dispatch")
    assert (await c.patch(f"/api/batches/{b['id']}", json={"quantity": 5})).status_code == 409
    assert (await c.patch(f"/api/batches/{b['id']}", json={"notes": "n"})).status_code == 200
    r = await c.post(f"/api/batches/{b['id']}/ungroup")
    assert r.status_code == 200 and r.json()["progress"]["total"] == 0
    queue = (await c.get("/api/queue")).json()
    assert len(queue) == 3 and all(e["batch_id"] is None and e["batch"] is None for e in queue)


async def test_batch_delete_cancels_and_removes_waiting(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    b = await _new_batch(c, quantity=3)
    await c.post(f"/api/batches/{b['id']}/dispatch")
    ids = [e["id"] for e in (await c.get(f"/api/queue?batch_id={b['id']}")).json()]
    with base.sync_session() as s:
        s.get(PrintQueueEntry, ids[0]).state = QueueState.DISPATCHED
        s.commit()
    assert (await c.delete(f"/api/batches/{b['id']}")).status_code == 204
    left = (await c.get(f"/api/queue?batch_id={b['id']}")).json()
    assert [e["id"] for e in left] == [ids[0]]
    assert (await c.get(f"/api/batches/{b['id']}")).json()["status"] == "cancelled"
    assert (await c.delete(f"/api/batches/{b['id']}")).status_code == 204  # second delete removes
    assert (await c.get(f"/api/batches/{b['id']}")).status_code == 404
    with base.sync_session() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "batch_delete")) is not None


async def test_batch_carries_ams_mapping_options_and_names(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    b = await _new_batch(
        c,
        quantity=2,
        ams_mapping=[0, 1],
        options={"bed_levelling": False, "flow_cali": True, "timelapse": True},
        copies_per_entry=2,
    )
    assert b["ams_mapping"] == [0, 1] and b["copies_per_entry"] == 2
    assert b["options"]["timelapse"] is True and b["model_name"] and b["file_name"]
    await c.post(f"/api/batches/{b['id']}/dispatch")
    with base.sync_session() as s:
        rows = s.scalars(select(PrintQueueEntry).where(PrintQueueEntry.batch_id == b["id"])).all()
        assert len(rows) == 2
        for e in rows:
            assert e.copies == 2 and e.ams_mapping == [0, 1]
            assert e.print_options == {"bed_levelling": False, "flow_cali": True, "timelapse": True}


def test_queued_commands_pass_printerd_validation():
    from app.printers import commands as cmds

    spec = cmds.DISPATCH["set_temperature"]
    for args in ({"target": "bed", "value": 60}, {"target": "chamber", "value": 45}):
        assert spec.validate(args) == args


def test_abandoned_preheat_releases_heater(redis_url: str, clock, sent):
    eid, pid = _gate_setup(redis_url, "ph-abandon")
    cfg = QueuePreheatConfig(enabled=True, bed_targets={"PLA": 60})
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 30})
    assert _gate(redis_url, eid, pid, cfg)[0] is False
    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        # still eligible and still wanted: kept
        assert queue_schedule.release_abandoned_preheats(s, client, cfg, {eid}) == 0
        # no longer eligible (held/removed): released
        assert queue_schedule.release_abandoned_preheats(s, client, cfg, set()) == 1
        assert s.get(PrintQueueEntry, eid).stage is None
    assert sent[-1] == (pid, "set_temperature", {"target": "bed", "value": 0})
    assert client.get(queue_schedule.preheat_key(eid)) is None
    client.close()


def test_override_off_releases_and_printer_change_releases_old(redis_url: str, clock, sent):
    eid, pid = _gate_setup(redis_url, "ph-abandon2")
    cfg = QueuePreheatConfig(enabled=True, bed_targets={"PLA": 60})
    _seed_state(redis_url, pid, {**IDLE_STATE, "bed_temper": 30})
    _gate(redis_url, eid, pid, cfg)
    with base.sync_session() as s:
        s.get(PrintQueueEntry, eid).preheat_override = "off"
        s.commit()
        pid2 = _seed_printer(s, name="OtherP").id
    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        assert queue_schedule.release_abandoned_preheats(s, client, cfg, {eid}) == 1
    assert sent[-1] == (pid, "set_temperature", {"target": "bed", "value": 0})
    with base.sync_session() as s:
        s.get(PrintQueueEntry, eid).preheat_override = None
        s.commit()
    _gate(redis_url, eid, pid, cfg)
    _gate(redis_url, eid, pid2, cfg)  # moved printers
    assert (pid, "set_temperature", {"target": "bed", "value": 0}) == sent[-2]
    assert sent[-1][0] == pid2
    client.close()


def test_keep_warm_disabled_releases_active(redis_url: str, clock, sent):
    with base.sync_session() as s:
        pid = _seed_printer(s, name="KWOffP").id
    client = redis_lib.Redis.from_url(redis_url)
    _seed_state(redis_url, pid, {**IDLE_STATE, "gcode_state": "FINISH"})
    on = QueuePreheatConfig(keep_warm_enabled=True)
    with base.sync_session() as s:
        queue_schedule.run_keep_warm(s, client, on, {pid})
        queue_schedule.run_keep_warm(s, client, QueuePreheatConfig(), {pid})
    assert sent[-1] == (pid, "set_temperature", {"target": "bed", "value": 0})
    assert client.get(queue_schedule.keepwarm_key(pid)) is None
    client.close()


def test_chamber_target_capped_at_65():
    with pytest.raises(ValueError):
        QueuePreheatConfig(chamber_targets={"ABS": 66})
    QueuePreheatConfig(chamber_targets={"ABS": 65}, bed_targets={"PETG": 120})


async def test_ungrouped_active_batch_reports_done(authenticated_client: httpx.AsyncClient):
    c = authenticated_client
    b = await _new_batch(c, quantity=2)
    await c.post(f"/api/batches/{b['id']}/dispatch")
    r = await c.post(f"/api/batches/{b['id']}/ungroup")
    assert r.json()["progress"]["total"] == 0 and r.json()["status"] == "done"
