"""P8 Home Assistant plan Task 6: energy accumulator snapshots at job
start/finish (`printerd.PrinterWorker._transition_active_job`) feeding
`costing.job_energy_kwh` -> `prints.energy_kwh`/`energy_cost`/`total_cost`
(`archive.archive_job`), and the P7 charge picking up the total.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import redis as redis_lib
import respx
from sqlalchemy import select

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import (
    CostCenter,
    LedgerEntry,
    Model,
    Print,
    PrintJob,
    PrintQueueEntry,
    Setting,
    User,
)
from app.models.enums import (
    ArchiveResult,
    HaRole,
    LedgerKind,
    PrinterKind,
    PrintJobState,
    QueueState,
    UserRole,
)
from app.models.home_assistant import HaLink
from app.models.printing import Printer
from app.printerd import PrinterWorker
from app.printers.base import PrinterConnection
from app.printers.fake import FakePrinterAdapter
from app.services import billing, ha_state
from app.services.archive import archive_job
from app.services.costing import job_energy_kwh
from app.services.ha_config import HaConfig
from app.services.home_assistant import poll_once
from app.tasks import base
from tests.cassettes import bambu_snapshots as cass

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")

BASE_URL = "http://homeassistant.local:8123"
TOKEN = "super-secret-long-lived-token"


@pytest.fixture(autouse=True)
def _clean_ha_redis_keys(redis_url: str):
    """Same cross-test hygiene as `tests/test_ha_hold.py` -- `redis_url` is
    session-scoped while Postgres sequences restart per test, so leftover
    `ha:*`/`printer:*:state` keys from one test could otherwise leak into a
    later test's printer that happens to land on the same id."""
    yield
    r = redis_lib.Redis.from_url(redis_url)
    try:
        for pattern in ("ha:*", "printer:*:state"):
            for key in r.scan_iter(pattern):
                r.delete(key)
    finally:
        r.close()


def _cfg(**overrides) -> HaConfig:
    fields = dict(
        ha_mqtt_url=None,
        ha_mqtt_user=None,
        ha_mqtt_password=None,
        ha_url=BASE_URL,
        ha_token=TOKEN,
    )
    fields.update(overrides)
    return HaConfig(**fields)


def _state_route(entity_id: str, state: str):
    return respx.get(f"{BASE_URL}/api/states/{entity_id}").mock(
        return_value=httpx.Response(200, json={"state": state, "attributes": {}})
    )


def _seed_printer(session, *, name="P1") -> Printer:
    p = Printer(
        name=name,
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.1",
        serial=name,
        access_code_enc=encrypt_secret(get_settings(), "12345678"),
        enabled=True,
    )
    session.add(p)
    session.commit()
    return p


# ---------------------------------------------------------------------------
# printerd: snapshot at start/finish
# ---------------------------------------------------------------------------


@pytest.fixture
def worker(redis_url, printer_enabled, migrated_db):
    settings = get_settings()
    with base.sync_session() as s:
        printer = Printer(
            name="p",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="S",
            access_code_enc="x",
            enabled=True,
        )
        s.add(printer)
        s.commit()
        s.refresh(printer)
        pid = printer.id
    adapter = FakePrinterAdapter(PrinterConnection(host="h", serial="S", access_code="x"))
    client = redis_lib.Redis.from_url(settings.redis_url)
    return PrinterWorker(settings, pid, adapter, client), adapter, pid, client


def test_printerd_snapshots_acc_at_start_and_end(worker):
    w, _adapter, pid, client = worker
    client.set(ha_state.energy_acc_key(pid), "5.0")
    w.handle_report(cass.SNAPSHOT_PRINTING)  # job created, PRINTING -> start snapshot
    with base.sync_session() as s:
        job = s.scalars(select(PrintJob).where(PrintJob.printer_id == pid)).first()
        job_id = job.id
        assert job.energy_start_kwh == pytest.approx(5.0)
        assert job.energy_end_kwh is None

    client.set(ha_state.energy_acc_key(pid), "5.6")
    w.handle_report(cass.SNAPSHOT_FINISH)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.energy_start_kwh == pytest.approx(5.0)
        assert job.energy_end_kwh == pytest.approx(5.6)


def test_printerd_redis_failure_never_blocks_transition(worker, monkeypatch):
    """`contextlib.suppress(Exception)` around the energy snapshot -- a
    Redis blip at job start/finish must not stop the state transition
    itself."""
    w, _adapter, pid, client = worker

    def _boom(_r, _pid):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(ha_state, "energy_acc", _boom)
    w.handle_report(cass.SNAPSHOT_PRINTING)
    with base.sync_session() as s:
        job = s.scalars(select(PrintJob).where(PrintJob.printer_id == pid)).first()
        assert job.state == PrintJobState.PRINTING.value
        assert job.energy_start_kwh is None


# ---------------------------------------------------------------------------
# counter reset across a poll mid-job (Task 4 poller + pure costing math)
# ---------------------------------------------------------------------------


@respx.mock
def test_counter_reset_mid_job(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    route = _state_route("sensor.plug_energy", "10.0")
    with base.sync_session() as s:
        printer = _seed_printer(s, name="EnergyResetP")
        s.add(
            HaLink(
                printer_id=printer.id,
                entity_id="sensor.plug_energy",
                role=HaRole.ENERGY_KWH,
            )
        )
        s.commit()
        pid = printer.id
        poll_once(s, r, _cfg())  # baseline: raw=10.0, acc=0.0

    start = ha_state.energy_acc(r, pid)
    assert start == pytest.approx(0.0)

    route.respond(200, json={"state": "10.5", "attributes": {}})
    with base.sync_session() as s:
        poll_once(s, r, _cfg())  # +0.5 -> acc=0.5

    route.respond(200, json={"state": "0.1", "attributes": {}})  # counter reset
    with base.sync_session() as s:
        poll_once(s, r, _cfg())  # reset: whole new reading -> acc=0.6

    end = ha_state.energy_acc(r, pid)
    assert end == pytest.approx(0.6)
    assert job_energy_kwh(start, end) == pytest.approx(0.6)
    r.close()


# ---------------------------------------------------------------------------
# archive costing: energy priced into total_cost, charged by billing
# ---------------------------------------------------------------------------


def _set_app_setting(s, **values) -> None:
    row = s.get(Setting, "app")
    if row is None:
        s.add(Setting(key="app", value=values))
    else:
        row.value = {**(row.value or {}), **values}
    s.commit()


def test_energy_cost_in_total(fake_adapter, sliced_3mf_bytes, seed):
    _set_app_setting(seed.s, energy_cost_per_kwh=0.30, billing_enabled=True)
    job_id = seed.job(subtask="Energy_plate_1", energy_start_kwh=5.0, energy_end_kwh=5.6)
    fake_adapter.files["/cache/Energy_plate_1.3mf"] = sliced_3mf_bytes

    user = User(username="energy-owner", role=UserRole.MEMBER)
    seed.s.add(user)
    seed.s.flush()
    cc = billing.ensure_private_center(seed.s, user.id)
    seed.s.commit()
    model = Model(slug="energy-billing", name="energy-billing")
    seed.s.add(model)
    seed.s.flush()
    entry = PrintQueueEntry(
        model_id=model.id,
        position=1,
        state=QueueState.DISPATCHED,
        print_job_id=job_id,
        cost_center_id=cc.id,
        created_by_user_id=user.id,
        reserved_cost=Decimal("999.00"),
    )
    seed.s.add(entry)
    seed.s.flush()
    billing.append(
        seed.s,
        cc.id,
        LedgerKind.RESERVATION,
        -Decimal("999.00"),
        queue_id=entry.id,
        user_id=user.id,
    )
    seed.s.commit()

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    seed.s.expire_all()  # sync_session is expire_on_commit=False

    run = seed.s.scalar(select(Print).where(Print.print_job_id == job_id))
    assert run.energy_kwh == pytest.approx(0.6)
    assert run.energy_cost == pytest.approx(0.18)
    assert run.total_cost == pytest.approx(
        (run.filament_cost or 0) + (run.machine_cost or 0) + 0.18
    )

    rows = list(
        seed.s.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.print_queue_id == entry.id)
            .order_by(LedgerEntry.id)
        ).all()
    )
    kinds = [row.kind for row in rows]
    assert kinds == [LedgerKind.RESERVATION, LedgerKind.RELEASE, LedgerKind.CHARGE]
    assert rows[2].amount == -billing.q2(run.total_cost)
    cc_after = seed.s.get(CostCenter, cc.id)
    assert cc_after.balance == -billing.q2(run.total_cost)


def test_no_ha_energy_is_none(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="NoEnergy_plate_1")  # energy_start/end_kwh left None
    fake_adapter.files["/cache/NoEnergy_plate_1.3mf"] = sliced_3mf_bytes

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)

    run = seed.s.scalar(select(Print).where(Print.print_job_id == job_id))
    assert run.energy_kwh is None
    assert run.energy_cost is None
    assert run.total_cost == pytest.approx((run.filament_cost or 0) + (run.machine_cost or 0))


def test_energy_redelivery_keeps_value(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="Redeliver_plate_1", energy_start_kwh=1.0, energy_end_kwh=2.0)
    fake_adapter.files["/cache/Redeliver_plate_1.3mf"] = sliced_3mf_bytes

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    run = seed.s.scalar(select(Print).where(Print.print_job_id == job_id))
    assert run.energy_kwh == pytest.approx(1.0)

    # A user edit to the stored value must survive a redelivery, exactly
    # like `run.filament_g`'s own protection.
    run.energy_kwh = 9.0
    seed.s.commit()

    # The underlying job's snapshots change (e.g. a later re-poll), but a
    # redelivered archive must not clobber the already-settled value.
    job = seed.s.get(PrintJob, job_id)
    job.energy_end_kwh = 5.0
    seed.s.commit()

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    seed.s.expire_all()
    run = seed.s.scalar(select(Print).where(Print.print_job_id == job_id))
    assert run.energy_kwh == pytest.approx(9.0)
