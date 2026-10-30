"""The P6 slicer/dispatch plan's dispatcher (Task 7): eligibility,
compatibility, fanout, copies-split, retry/reconcile, and the beat task's
lock + off-switch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import redis as redis_lib

from app.config import get_settings
from app.models.enums import (
    BlobFormat,
    BlobKind,
    Fanout,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
    ProfileKind,
    QueueState,
    UserRole,
)
from app.models.library import Blob, File, Model, PrintQueueEntry, Revision
from app.models.printing import Printer, PrintJob
from app.models.processing import BlobMeta
from app.models.slicing import PrintRecipe, SlicerProfile
from app.printers.base import state_key
from app.security import hash_password
from app.services import dispatcher
from app.services.ams_match import FilamentNeed
from app.services.dispatcher import Candidate, Needs, compatibility_reason, select_printers
from app.services.printer_state import dispatch_idle
from app.tasks import base
from app.tasks import dispatch as dispatch_task

pytestmark = pytest.mark.usefixtures("data_dir", "printer_enabled", "library_root")


def _seed_model_file(session, *, slug: str, printer_model="Bambu Lab A1 mini", nozzle=0.4):
    model = Model(slug=slug, name=slug)
    session.add(model)
    session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001")
    session.add(revision)
    session.flush()

    content = b"fake-gcode-3mf-" + slug.encode()
    from blake3 import blake3

    digest = blake3(content).hexdigest()
    blob = Blob(hash=digest, size=len(content), kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF)
    session.add(blob)
    session.flush()
    file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path=f"{slug}.gcode.3mf",
        storage_path="x",
        verified_at=None,
    )
    session.add(file)
    session.flush()
    session.add(
        BlobMeta(
            blob_hash=digest,
            printer_model=printer_model,
            nozzle=nozzle,
            raw={
                "plates": [
                    {
                        "index": 1,
                        "filaments": [
                            {"filament_id": 1, "type": "PLA", "color": "#FF0000", "used_g": 10}
                        ],
                    }
                ]
            },
        )
    )
    session.commit()
    return model, revision, file


def _seed_printer(session, *, name="P1", model="A1 mini", enabled=True) -> Printer:
    from app.crypto import encrypt_secret

    p = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial=name,
        access_code_enc=encrypt_secret(get_settings(), "12345678"),
        model=model,
        enabled=enabled,
    )
    session.add(p)
    session.commit()
    return p


def _seed_entry(
    session, *, model, file, printer_id=None, recipe_id=None, state, copies=1, created_by=None
) -> PrintQueueEntry:
    max_position = 0
    from sqlalchemy import func, select

    max_position = session.scalar(select(func.max(PrintQueueEntry.position))) or 0
    entry = PrintQueueEntry(
        model_id=model.id,
        file_id=file.id,
        sliced_file_id=file.id,
        printer_id=printer_id,
        recipe_id=recipe_id,
        plate=1,
        copies=copies,
        state=state,
        position=max_position + 1,
        created_by_user_id=created_by,
    )
    session.add(entry)
    session.commit()
    return entry


def _seed_admin(session) -> int:
    from app.models.auth import User

    user = User(
        username=f"admin-{id(session)}-{__import__('uuid').uuid4().hex[:8]}",
        password_hash=hash_password("x"),
        role=UserRole.ADMIN,
    )
    session.add(user)
    session.commit()
    return user.id


def _seed_member_with_access(session, printer_ids: list[int]) -> int:
    from app.models.auth import Group, GroupMember, PrinterAccess, User

    user = User(
        username=f"member-{__import__('uuid').uuid4().hex[:8]}",
        password_hash=hash_password("x"),
        role=UserRole.MEMBER,
    )
    session.add(user)
    session.flush()
    group = Group(name=f"g-{user.username}", permissions=["printers:print"])
    session.add(group)
    session.flush()
    session.add(GroupMember(group_id=group.id, user_id=user.id))
    for pid in printer_ids:
        session.add(PrinterAccess(group_id=group.id, printer_id=pid, can_print=True))
    session.commit()
    return user.id


def _seed_state(redis_url: str, printer_id: int, state: dict) -> None:
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(printer_id), json.dumps(state))
    c.close()


IDLE_STATE = {
    "gcode_state": "IDLE",
    "trays": [{"slot": 0, "ams_unit": 0, "tray": 0, "tray_type": "PLA", "color": "#FF0000"}],
    "nozzle_mm": 0.4,
}


# ---------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fanout,n,idle_mask,copies,expected_len",
    [
        (Fanout.SINGLE, None, [True], 1, 1),
        (Fanout.SINGLE, None, [True, True], 3, 1),
        (Fanout.SINGLE, None, [False], 1, 0),
        (Fanout.SPREAD, None, [True, True], 2, 2),
        (Fanout.SPREAD, None, [True, False], 2, 1),
        (Fanout.SPREAD, None, [True, True], 1, 1),
        (Fanout.FIRST_N, 1, [True, True], 2, 1),
        (Fanout.FIRST_N, 2, [True, True], 2, 2),
        (Fanout.FIRST_N, 1, [False, True], 2, 0),  # truncated to the FIRST printer only
    ],
)
def test_select_printers_matrix(fanout, n, idle_mask, copies, expected_len):
    candidates = [Candidate(id=i + 1, idle=idle) for i, idle in enumerate(idle_mask)]
    result = select_printers(fanout, n, candidates, copies)
    assert len(result) == expected_len
    assert len(set(result)) == len(result)


def test_compat_rejects_model_nozzle_material():
    printer = Printer(
        name="p",
        kind=PrinterKind.BAMBU_LAN,
        host="h",
        serial="s",
        access_code_enc="x",
        model="A1 mini",
    )
    ok_need = Needs(
        printer_model="Bambu Lab A1 mini",
        nozzle_mm=0.4,
        filaments=[FilamentNeed(filament_id=1, type="PLA", color="#FF0000", grams=1)],
    )
    state = {"gcode_state": "IDLE", "nozzle_mm": 0.4, "trays": [{"slot": 0, "tray_type": "PLA"}]}
    assert compatibility_reason(printer, state, ok_need) is None

    model_mismatch = Needs(printer_model="Bambu Lab X1", nozzle_mm=None, filaments=[])
    assert compatibility_reason(printer, state, model_mismatch) is not None

    nozzle_need = Needs(printer_model=None, nozzle_mm=0.8, filaments=[])
    assert compatibility_reason(printer, state, nozzle_need) is not None

    nozzle_unknown_state = {"gcode_state": "IDLE", "trays": []}
    assert compatibility_reason(printer, nozzle_unknown_state, nozzle_need) is None  # skipped

    material_need = Needs(
        printer_model=None,
        nozzle_mm=None,
        filaments=[FilamentNeed(filament_id=1, type="ABS", color=None, grams=1)],
    )
    assert compatibility_reason(printer, state, material_need) is not None


def test_finish_is_idle_failed_is_not():
    assert dispatch_idle({"gcode_state": "IDLE"}) is True
    assert dispatch_idle({"gcode_state": "FINISH"}) is True
    assert dispatch_idle({"gcode_state": "FAILED"}) is False
    assert dispatch_idle(None) is False


# ---------------------------------------------------------------------------
# dispatch_once
# ---------------------------------------------------------------------------


def test_copies_split_single_pins_printer(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="copies-single")
        printer = _seed_printer(s, name="SingleP")
        admin_id = _seed_admin(s)
        entry = _seed_entry(
            s, model=model, file=file, state=QueueState.READY, copies=3, created_by=admin_id
        )
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["sent"] == 1
    with base.sync_session() as s:
        from sqlalchemy import select

        sent = s.get(PrintQueueEntry, entry_id)
        assert sent.state == QueueState.DISPATCHED
        assert sent.copies == 1
        assert sent.assigned_printer_id == pid
        # P7: no cost center on this item -- the clone copies the (NULL)
        # review/billing columns through untouched, no transfer posted.
        assert sent.cost_center_id is None
        assert sent.reserved_cost is None

        clones = list(
            s.scalars(
                select(PrintQueueEntry).where(
                    PrintQueueEntry.id != entry_id, PrintQueueEntry.model_id == model.id
                )
            )
        )
        assert len(clones) == 1
        assert clones[0].copies == 2
        assert clones[0].state == QueueState.READY
        assert clones[0].printer_id == pid  # single fanout pins the clone
        assert clones[0].cost_center_id is None
        assert clones[0].reserved_cost is None
        assert clones[0].review_state == sent.review_state


def test_one_item_per_printer_per_tick(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        printer = _seed_printer(s, name="SharedP")
        admin_id = _seed_admin(s)
        model_a, _, file_a = _seed_model_file(s, slug="one-per-a")
        model_b, _, file_b = _seed_model_file(s, slug="one-per-b")
        entry_a = _seed_entry(
            s, model=model_a, file=file_a, state=QueueState.READY, created_by=admin_id
        )
        entry_b = _seed_entry(
            s, model=model_b, file=file_b, state=QueueState.READY, created_by=admin_id
        )
        pid = printer.id
        ids = (entry_a.id, entry_b.id)
    _seed_state(redis_url, pid, IDLE_STATE)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["sent"] == 1
    with base.sync_session() as s:
        states = [s.get(PrintQueueEntry, i).state for i in ids]
    assert states.count(QueueState.DISPATCHED) == 1
    assert states.count(QueueState.READY) == 1


def test_held_not_dispatched_state_held(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="held-state")
        printer = _seed_printer(s, name="HeldStateP")
        admin_id = _seed_admin(s)
        entry = _seed_entry(s, model=model, file=file, state=QueueState.HELD, created_by=admin_id)
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["sent"] == 0
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.HELD


def test_held_not_dispatched_printer_hold_reason(
    redis_url: str, fake_adapter, monkeypatch: pytest.MonkeyPatch
):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="held-printer")
        printer = _seed_printer(s, name="HoldReasonP")
        admin_id = _seed_admin(s)
        entry = _seed_entry(s, model=model, file=file, state=QueueState.READY, created_by=admin_id)
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)
    monkeypatch.setattr(dispatcher, "printer_hold_reason", lambda client, printer_id: "paused")

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["sent"] == 0
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.READY


def test_revoked_access_holds(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="revoked")
        printer = _seed_printer(s, name="RevokedP")
        member_id = _seed_member_with_access(s, [])  # no printer access at all
        entry = _seed_entry(
            s,
            model=model,
            file=file,
            printer_id=printer.id,
            state=QueueState.READY,
            created_by=member_id,
        )
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["held"] == 1
    with base.sync_session() as s:
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.state == QueueState.HELD
        assert refreshed.last_error


def test_send_failure_retry_create_job_sync_raises(
    redis_url: str, fake_adapter, monkeypatch: pytest.MonkeyPatch
):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="retry-raise")
        printer = _seed_printer(s, name="RetryRaiseP")
        admin_id = _seed_admin(s)
        entry = _seed_entry(s, model=model, file=file, state=QueueState.READY, created_by=admin_id)
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    def _boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(dispatcher, "create_job_sync", _boom)

    client = redis_lib.Redis.from_url(redis_url)
    for expected_attempts in (1, 2, 3):
        with base.sync_session() as s:
            dispatcher.dispatch_once(s, client, settings)
        with base.sync_session() as s:
            refreshed = s.get(PrintQueueEntry, entry_id)
            assert refreshed.attempts == expected_attempts
            if expected_attempts < 3:
                assert refreshed.state == QueueState.READY
            else:
                assert refreshed.state == QueueState.FAILED
    client.close()


def test_send_failure_retry_job_failed_before_start(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="retry-failed-job")
        printer = _seed_printer(s, name="RetryFailedJobP")
        admin_id = _seed_admin(s)
        job = PrintJob(
            printer_id=printer.id,
            file_id=file.id,
            origin=PrintJobOrigin.APP,
            state=PrintJobState.FAILED,
            printer_error="upload failed",
        )
        s.add(job)
        s.flush()
        entry = _seed_entry(
            s, model=model, file=file, state=QueueState.DISPATCHED, created_by=admin_id
        )
        entry.print_job_id = job.id
        entry.assigned_printer_id = printer.id
        s.commit()
        entry_id, pid = entry.id, printer.id
    # Busy (not idle) for this tick -- isolates the reconcile step from the
    # main loop's own re-dispatch, which would otherwise immediately pick
    # this now-`ready` item right back up in the SAME tick.
    _seed_state(redis_url, pid, {**IDLE_STATE, "gcode_state": "RUNNING"})

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["retried"] == 1
    with base.sync_session() as s:
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.attempts == 1
        assert refreshed.state == QueueState.READY
        assert refreshed.last_error == "upload failed"


def test_reconcile_fails_stale_uploading_job_and_retries(redis_url: str, fake_adapter):
    """A worker lost between claim and upload leaves the job UPLOADING; once
    stale, reconcile fails it so the item retries and the printer unblocks."""
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="stale-uploading")
        printer = _seed_printer(s, name="StaleUploadingP")
        admin_id = _seed_admin(s)
        stale = PrintJob(
            printer_id=printer.id,
            file_id=file.id,
            origin=PrintJobOrigin.APP,
            state=PrintJobState.UPLOADING,
            created_at=datetime.now(UTC) - timedelta(seconds=dispatcher.UPLOADING_STALE_S + 60),
        )
        fresh = PrintJob(
            printer_id=printer.id,
            file_id=file.id,
            origin=PrintJobOrigin.APP,
            state=PrintJobState.UPLOADING,
        )
        s.add_all([stale, fresh])
        s.flush()
        entry = _seed_entry(
            s, model=model, file=file, state=QueueState.DISPATCHED, created_by=admin_id
        )
        entry.print_job_id = stale.id
        entry.assigned_printer_id = printer.id
        s.commit()
        entry_id, pid, stale_id, fresh_id = entry.id, printer.id, stale.id, fresh.id
    _seed_state(redis_url, pid, {**IDLE_STATE, "gcode_state": "RUNNING"})

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["retried"] == 1
    with base.sync_session() as s:
        assert s.get(PrintJob, stale_id).state == PrintJobState.FAILED
        assert s.get(PrintJob, fresh_id).state == PrintJobState.UPLOADING
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.attempts == 1
        assert refreshed.state == QueueState.READY


def test_create_job_sync_marks_job_failed_on_apply_async_error(
    redis_url: str, fake_adapter, monkeypatch: pytest.MonkeyPatch
):
    """I4: if `apply_async` itself blows up (broker unreachable), the job
    must be marked FAILED with `started_at=None` instead of left QUEUED
    forever -- `started_at=None` is what the next reconcile tick's retry
    step keys off."""
    from app.schemas.printers import PrintRequest
    from app.services import print_plan

    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="apply-async-raises")
        printer = _seed_printer(s, name="ApplyAsyncRaisesP")
        pid = printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    def _boom(*args, **kwargs):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr("app.tasks.printing.send_to_printer.apply_async", _boom)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        req = PrintRequest(file_id=file.id, plate=1, use_ams=False, ams_mapping=[])
        job = print_plan.create_job_sync(s, settings, client, pid, req, None)
        job_id = job.id
    client.close()

    with base.sync_session() as s:
        refreshed = s.get(PrintJob, job_id)
        assert refreshed.state == PrintJobState.FAILED
        assert refreshed.started_at is None
        assert "broker unreachable" in (refreshed.printer_error or "")


def test_reconcile_job_finished_marks_entry_done(redis_url: str, fake_adapter):
    """I5: a FINISHED job means the print actually completed."""
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="reconcile-finished")
        printer = _seed_printer(s, name="ReconcileFinishedP")
        job = PrintJob(printer_id=printer.id, file_id=file.id, state=PrintJobState.FINISHED)
        s.add(job)
        s.flush()
        entry = _seed_entry(s, model=model, file=file, state=QueueState.DISPATCHED)
        entry.print_job_id = job.id
        entry.assigned_printer_id = printer.id
        s.commit()
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["done"] == 1
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.DONE


def test_reconcile_job_canceled_marks_entry_failed_no_retry(redis_url: str, fake_adapter):
    """I5: CANCELED is final -- no retry, straight to `failed`."""
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="reconcile-canceled")
        printer = _seed_printer(s, name="ReconcileCanceledP")
        job = PrintJob(printer_id=printer.id, file_id=file.id, state=PrintJobState.CANCELED)
        s.add(job)
        s.flush()
        entry = _seed_entry(s, model=model, file=file, state=QueueState.DISPATCHED)
        entry.print_job_id = job.id
        entry.assigned_printer_id = printer.id
        s.commit()
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, {**IDLE_STATE, "gcode_state": "RUNNING"})

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["retried"] == 0
    with base.sync_session() as s:
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.state == QueueState.FAILED
        assert refreshed.attempts == 0


def test_reconcile_job_failed_after_started_no_retry(redis_url: str, fake_adapter):
    """I5: FAILED with `started_at` set (picked up, then failed mid-print)
    is also final -- unlike FAILED with `started_at IS NULL`."""
    settings = get_settings()
    import datetime as dt

    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="reconcile-failed-started")
        printer = _seed_printer(s, name="ReconcileFailedStartedP")
        job = PrintJob(
            printer_id=printer.id,
            file_id=file.id,
            state=PrintJobState.FAILED,
            started_at=dt.datetime.now(dt.UTC),
            printer_error="nozzle clog",
        )
        s.add(job)
        s.flush()
        entry = _seed_entry(s, model=model, file=file, state=QueueState.DISPATCHED)
        entry.print_job_id = job.id
        entry.assigned_printer_id = printer.id
        s.commit()
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, {**IDLE_STATE, "gcode_state": "RUNNING"})

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["retried"] == 0
    with base.sync_session() as s:
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.state == QueueState.FAILED
        assert refreshed.attempts == 0
        assert refreshed.last_error == "nozzle clog"


def test_reconcile_stale_slicing_goes_back_to_waiting(redis_url: str, fake_adapter):
    """I2: a `slicing` entry stuck well past SLICING_STALE_S (worker
    crash/kill mid-slice) is bounced back to `waiting`, attempt NOT
    consumed."""
    import datetime as dt

    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="stale-slicing")
        entry = _seed_entry(s, model=model, file=file, state=QueueState.WAITING)
        entry.state = QueueState.SLICING
        entry.slicing_started_at = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=dispatcher.SLICING_STALE_S + 60
        )
        s.commit()
        entry_id = entry.id

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        dispatcher.dispatch_once(s, client, settings)
    client.close()

    with base.sync_session() as s:
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.state == QueueState.WAITING
        assert refreshed.attempts == 0
        assert refreshed.slicing_started_at is None


def test_no_matching_printer_stays_ready_not_held(redis_url: str, fake_adapter):
    """M1: an item whose own printer target (here, its recipe's
    `target_printer_model`) matches no printer at all stays `ready` with a
    plain "no matching printer" error -- never `held` (that's reserved for
    an access problem)."""
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="no-match")
        admin_id = _seed_admin(s)
        printer_profile = SlicerProfile(name="np-pp", kind=ProfileKind.PRINTER, data={})
        process_profile = SlicerProfile(name="np-pc", kind=ProfileKind.PROCESS, data={})
        s.add_all([printer_profile, process_profile])
        s.flush()
        recipe = PrintRecipe(
            name="no-match-recipe",
            printer_profile_id=printer_profile.id,
            process_profile_id=process_profile.id,
            filament_profile_ids=[],
            bed_type="textured_plate",
            options={},
            target_printer_model="No Such Printer Model",
        )
        s.add(recipe)
        s.flush()
        entry = _seed_entry(
            s,
            model=model,
            file=file,
            recipe_id=recipe.id,
            state=QueueState.READY,
            created_by=admin_id,
        )
        entry_id = entry.id

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["held"] == 0
    with base.sync_session() as s:
        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.state == QueueState.READY
        assert refreshed.last_error == "no matching printer"


def test_failed_primary_send_keeps_full_copies_no_clone(
    redis_url: str, fake_adapter, monkeypatch: pytest.MonkeyPatch
):
    """M2: when the primary send itself fails, nothing is split off -- the
    item keeps its original copy count for its own retry, with no leftover
    clone alongside it."""
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="failed-primary-copies")
        printer = _seed_printer(s, name="FailedPrimaryCopiesP")
        admin_id = _seed_admin(s)
        entry = _seed_entry(
            s, model=model, file=file, state=QueueState.READY, copies=3, created_by=admin_id
        )
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    def _boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(dispatcher, "create_job_sync", _boom)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        dispatcher.dispatch_once(s, client, settings)
    client.close()

    with base.sync_session() as s:
        from sqlalchemy import select

        refreshed = s.get(PrintQueueEntry, entry_id)
        assert refreshed.copies == 3
        assert refreshed.state == QueueState.READY
        clones = list(
            s.scalars(
                select(PrintQueueEntry).where(
                    PrintQueueEntry.id != entry_id, PrintQueueEntry.model_id == model.id
                )
            )
        )
        assert clones == []


def test_leftover_clone_keeps_items_position_ahead_of_later_items(redis_url: str, fake_adapter):
    """I6: 3 copies on a single idle printer with other items already
    queued behind it -- the leftover clone (2 copies still pending) must
    land ahead of those other items, not appended behind them."""
    settings = get_settings()
    with base.sync_session() as s:
        printer = _seed_printer(s, name="LeftoverOrderP")
        admin_id = _seed_admin(s)
        model_a, _, file_a = _seed_model_file(s, slug="leftover-a")
        entry_a = _seed_entry(
            s, model=model_a, file=file_a, state=QueueState.READY, copies=3, created_by=admin_id
        )
        model_b, _, file_b = _seed_model_file(s, slug="leftover-b")
        entry_b = _seed_entry(
            s, model=model_b, file=file_b, state=QueueState.READY, created_by=admin_id
        )
        pid = printer.id
        entry_a_id, entry_b_id = entry_a.id, entry_b.id
        entry_a_position = entry_a.position
    _seed_state(redis_url, pid, IDLE_STATE)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        dispatcher.dispatch_once(s, client, settings)
    client.close()

    with base.sync_session() as s:
        from sqlalchemy import select

        clone = s.scalar(
            select(PrintQueueEntry).where(
                PrintQueueEntry.model_id == model_a.id, PrintQueueEntry.id != entry_a_id
            )
        )
        entry_b = s.get(PrintQueueEntry, entry_b_id)
        assert clone is not None
        assert clone.copies == 2
        assert clone.position == entry_a_position
        assert entry_b.position > clone.position


def test_waiting_recipe_kicks_slice(redis_url: str, monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="waiting-recipe")
        printer_profile = SlicerProfile(name="pp", kind=ProfileKind.PRINTER, data={})
        process_profile = SlicerProfile(name="pc", kind=ProfileKind.PROCESS, data={})
        s.add_all([printer_profile, process_profile])
        s.flush()
        recipe = PrintRecipe(
            name="r1",
            printer_profile_id=printer_profile.id,
            process_profile_id=process_profile.id,
            filament_profile_ids=[None],
            bed_type="textured_plate",
            options={},
        )
        s.add(recipe)
        s.flush()
        admin_id = _seed_admin(s)
        entry = PrintQueueEntry(
            model_id=model.id,
            file_id=file.id,
            sliced_file_id=None,
            recipe_id=recipe.id,
            plate=1,
            copies=1,
            state=QueueState.WAITING,
            position=1,
            created_by_user_id=admin_id,
        )
        s.add(entry)
        s.commit()
        entry_id = entry.id

    calls: list[int] = []
    monkeypatch.setattr("app.tasks.slicing.slice_file.delay", lambda qid: calls.append(qid))

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()

    assert counts["sliced"] == 1
    assert calls == [entry_id]
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.SLICING


# ---------------------------------------------------------------------------
# beat task: lock + off-switch
# ---------------------------------------------------------------------------


def test_lock_skips_overlap(redis_url: str, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(dispatch_task, "dispatch_once", lambda *a, **k: calls.append(1))

    client = redis_lib.Redis.from_url(redis_url)
    lock = client.lock(dispatch_task.DISPATCH_LOCK_KEY, timeout=60, blocking=False)
    assert lock.acquire(blocking=False)
    try:
        result = dispatch_task.dispatch_queue()
    finally:
        lock.release()
        client.close()

    assert result is None
    assert calls == []


def test_dispatch_queue_noop_when_printer_disabled(redis_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PRINTER_ENABLED", raising=False)
    get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(dispatch_task, "dispatch_once", lambda *a, **k: calls.append(1))

    result = dispatch_task.dispatch_queue()

    assert result is None
    assert calls == []
    get_settings.cache_clear()


def test_maintenance_mode_printer_is_not_dispatched(redis_url: str, fake_adapter):
    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="maint-mode")
        printer = _seed_printer(s, name="MaintP")
        printer.maintenance_mode = True
        s.commit()
        admin_id = _seed_admin(s)
        entry = _seed_entry(
            s, model=model, file=file, state=QueueState.READY, copies=1, created_by=admin_id
        )
        entry_id, pid = entry.id, printer.id
    _seed_state(redis_url, pid, IDLE_STATE)

    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    assert counts["sent"] == 0
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.READY
        p = s.get(Printer, pid)
        p.maintenance_mode = False
        s.commit()
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()
    assert counts["sent"] == 1


def _rest_plug(session, pid, **kw):
    import json as _json

    from app.crypto import encrypt_secret
    from app.models import SmartPlug

    cfg = {
        "on_url": "http://plug/on",
        "off_url": "http://plug/off",
        "status_url": "http://plug/st",
        "state_path": "relay",
    }
    plug = SmartPlug(
        printer_id=pid,
        kind="rest",
        config_enc=encrypt_secret(get_settings(), _json.dumps(cfg)),
        **kw,
    )
    session.add(plug)
    session.commit()
    return plug.id


def test_wake_only_placeable_printers_and_waits_for_fresh_report(redis_url: str, fake_adapter):
    import respx

    from app.models import SmartPlug
    from app.services.smart_plugs import manager

    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="wake-1")
        target = _seed_printer(s, name="Target")
        other = _seed_printer(s, name="Other")
        admin_id = _seed_admin(s)
        _seed_entry(
            s,
            model=model,
            file=file,
            state=QueueState.READY,
            printer_id=target.id,
            created_by=admin_id,
        )
        tplug = _rest_plug(s, target.id, auto_on_before_print=True, last_state="off")
        oplug = _rest_plug(s, other.id, auto_on_before_print=True, last_state="off")
        tid, oid = target.id, other.id
    client = redis_lib.Redis.from_url(redis_url)
    # stale IDLE state from before the printer was powered off
    _seed_state(redis_url, tid, {**IDLE_STATE, "last_report_at": "2020-01-01T00:00:00+00:00"})

    with respx.mock:
        on = respx.post("http://plug/on").respond(200)
        with base.sync_session() as s:
            counts = dispatcher.dispatch_once(s, client, settings)
        assert on.call_count == 1  # only the pinned target's plug
    assert counts["sent"] == 0  # stale state: printer treated as booting
    with base.sync_session() as s:
        assert s.get(SmartPlug, tplug).last_state == "on"
        assert s.get(SmartPlug, oplug).last_state == "off"
    assert client.get(manager.woken_key(tid)) is not None
    assert client.get(manager.woken_key(oid)) is None

    # a fresh report arrives -> dispatch proceeds and the marker clears
    from datetime import UTC, datetime

    _seed_state(redis_url, tid, {**IDLE_STATE, "last_report_at": datetime.now(UTC).isoformat()})
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    assert counts["sent"] == 1
    assert client.get(manager.woken_key(tid)) is None
    client.close()


def test_boot_wait_is_capped(redis_url: str):
    import time

    from app.services.smart_plugs import manager

    client = redis_lib.Redis.from_url(redis_url)
    client.set(manager.woken_key(77), repr(time.time() - manager.BOOT_WAIT_S - 5))
    assert dispatcher._still_booting(client, 77, {"last_report_at": None}) is False
    assert client.get(manager.woken_key(77)) is None
    client.close()


def test_no_off_on_cycle_when_queue_wants_printer(redis_url: str, fake_adapter):
    from datetime import UTC, datetime, timedelta

    import respx

    from app.services.smart_plugs import manager

    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug="nocycle")
        printer = _seed_printer(s, name="Cycle")
        admin_id = _seed_admin(s)
        # a READY item pinned to a different (busy-forever) printer must not matter;
        # this one targets our printer but it is held by an HA hold, so stays queued
        _seed_entry(
            s,
            model=model,
            file=file,
            state=QueueState.READY,
            printer_id=printer.id,
            created_by=admin_id,
        )
        _rest_plug(s, printer.id, auto_off_after_min=1, last_state="on")
        pid = printer.id
    client = redis_lib.Redis.from_url(redis_url)
    client.set(
        state_key(pid), json.dumps({**IDLE_STATE, "gcode_state": "FINISH", "nozzle_temper": 25.0})
    )
    t0 = datetime.now(UTC)
    with respx.mock:
        respx.get("http://plug/st").respond(json={"relay": True})
        off = respx.post("http://plug/off").respond(200)
        with base.sync_session() as s:
            manager.tick(s, client, settings, now=t0)
            manager.tick(s, client, settings, now=t0 + timedelta(minutes=5))
        assert not off.called  # queue needs it
    client.close()


def _ready_item(redis_url: str, slug: str):
    with base.sync_session() as s:
        model, _, file = _seed_model_file(s, slug=slug)
        printer = _seed_printer(s, name=f"P-{slug}")
        admin_id = _seed_admin(s)
        entry = _seed_entry(s, model=model, file=file, state=QueueState.READY, created_by=admin_id)
        ids = (entry.id, printer.id)
    _seed_state(redis_url, ids[1], IDLE_STATE)
    return ids


def test_dispatch_same_item_twice_sends_once(redis_url: str, fake_adapter):
    """A stale in-memory READY item (e.g. from an overlapping tick) must not send twice."""
    from sqlalchemy import func, select

    settings = get_settings()
    entry_id, pid = _ready_item(redis_url, "dbl")
    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s1, base.sync_session() as s2:
        a = s1.get(PrintQueueEntry, entry_id)
        b = s2.get(PrintQueueEntry, entry_id)
        need = dispatcher._needs_for_item(s1, a)
        assert dispatcher._dispatch(s1, settings, client, a, [pid], Fanout.SINGLE, need) is True
        assert dispatcher._dispatch(s2, settings, client, b, [pid], Fanout.SINGLE, need) is False
    client.close()
    with base.sync_session() as s:
        assert s.scalar(select(func.count()).select_from(PrintJob)) == 1


def test_dispatch_skips_item_canceled_after_selection(redis_url: str, fake_adapter):
    from sqlalchemy import func, select

    settings = get_settings()
    entry_id, pid = _ready_item(redis_url, "cxl")
    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s1, base.sync_session() as s2:
        a = s1.get(PrintQueueEntry, entry_id)
        need = dispatcher._needs_for_item(s1, a)
        s2.get(PrintQueueEntry, entry_id).state = QueueState.FAILED
        s2.commit()
        assert dispatcher._dispatch(s1, settings, client, a, [pid], Fanout.SINGLE, need) is False
    client.close()
    with base.sync_session() as s:
        assert s.scalar(select(func.count()).select_from(PrintJob)) == 0
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.FAILED


def test_dispatch_once_stops_when_lock_lost(redis_url: str, fake_adapter):
    settings = get_settings()
    entry_id, _ = _ready_item(redis_url, "lck")
    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings, keepalive=lambda: False)
    client.close()
    assert counts["sent"] == 0
    with base.sync_session() as s:
        assert s.get(PrintQueueEntry, entry_id).state == QueueState.READY
