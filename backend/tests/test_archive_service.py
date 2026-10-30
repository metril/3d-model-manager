import ftplib
import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Model, Print, PrintArchive, Printer, PrintJob, Spool, SpoolUsage
from app.models.enums import ArchiveResult, SpoolUsageSource
from app.services import archive as archive_mod
from app.services.archive import ArchiveFetchError, archive_job, archive_path
from tests.archive_fixtures import spool_on_tray

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")


def _run(s, job_id):
    return s.scalar(select(Print).where(Print.print_job_id == job_id))


def test_archive_external_job_from_ftps(fake_adapter, sliced_3mf_bytes, seed):
    s, settings = seed.s, get_settings()
    model_id = seed.model("DotC parts - Part 1")
    job_id = seed.job(subtask="DotC parts - Part 1_plate_1")
    fake_adapter.files["/cache/DotC parts - Part 1_plate_1.3mf"] = sliced_3mf_bytes
    arc = archive_job(s, settings, job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "done" and arc.plate == 1 and arc.model_id == model_id
    assert arc.est_filament == [
        {
            "slot": 0,
            "filament_id": 1,
            "type": "PLA",
            "color": "#000000",
            "grams": 61.64,
            "meters": 21.18,
        }
    ]
    assert archive_path(settings, arc.archive_blob, "gcode.3mf").exists()
    assert arc.archive_blob == hashlib.sha256(sliced_3mf_bytes).hexdigest()
    assert arc.thumbnail_blob is not None and arc.duration_s == 3600
    assert archive_path(settings, arc.thumbnail_blob, "png").exists()
    run = _run(s, job_id)
    assert run.source == "auto" and run.result == "success" and run.created_by_user_id is None
    assert run.filament_g == pytest.approx(61.64)
    assert run.filament_cost == pytest.approx(61.64 / 1000 * 20.0)
    assert s.get(Model, model_id).print_count == 1


def test_archive_app_job_uses_local_blob(fake_adapter, sliced_3mf_bytes, seed, monkeypatch):
    s, settings = seed.s, get_settings()
    sha = hashlib.sha256(sliced_3mf_bytes).hexdigest()
    model_id, rev_id, file_id = seed.file("Widget", sha)
    uid = seed.user()

    def _fetch(session, settings, blob_hash, dest_dir, suffix):
        p = dest_dir / f"blob{suffix}"
        p.write_bytes(sliced_3mf_bytes)
        return p

    monkeypatch.setattr(archive_mod, "fetch_blob_to_temp", _fetch)
    job_id = seed.job(origin="app", file_id=file_id, plate=1, user_id=uid)
    arc = archive_job(s, settings, job_id, ArchiveResult.COMPLETED)
    assert fake_adapter.downloads == [] and arc.revision_id == rev_id
    assert arc.model_id == model_id and arc.plate == 1
    run = _run(s, job_id)
    assert run.created_by_user_id == uid == arc.created_by_user_id


def test_archive_app_job_falls_back_to_cache(fake_adapter, sliced_3mf_bytes, seed, monkeypatch):
    s = seed.s
    sha = hashlib.sha256(sliced_3mf_bytes).hexdigest()
    _, _, file_id = seed.file("Widget", sha)

    def _fetch(*a, **k):
        raise LookupError

    monkeypatch.setattr(archive_mod, "fetch_blob_to_temp", _fetch)
    job_id = seed.job(origin="app", file_id=file_id)
    fake_adapter.files[f"/cache/tdmm-{job_id}.gcode.3mf"] = sliced_3mf_bytes
    arc = archive_job(s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "done" and arc.plate == 1


def test_archive_partial_cost_on_cancel(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1", layer=237, total_layers=474)
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.CANCELED)
    run = _run(seed.s, job_id)
    assert run.filament_g == pytest.approx(30.82) and run.result == "partial"


def test_archive_missing_file(fake_adapter, seed):
    job_id = seed.job(subtask="Nope")
    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "missing" and arc.est_filament == []
    run = _run(seed.s, job_id)
    assert run.filament_cost is None and run.machine_cost is not None


def test_archive_fetch_error_raises_after_commit(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    fake_adapter.download_errors = 1
    with pytest.raises(ArchiveFetchError):
        archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    seed.s.expire_all()
    arc = seed.s.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job_id))
    assert arc.fetch_state == "error" and "s3cretcode" not in (arc.fetch_error or "")


def test_archive_is_idempotent(fake_adapter, sliced_3mf_bytes, seed):
    model_id = seed.model("X")
    job_id = seed.job(subtask="X_plate_1")
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    for _ in range(2):
        archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert len(seed.s.scalars(select(PrintArchive)).all()) == 1
    assert len(seed.s.scalars(select(Print)).all()) == 1
    assert seed.s.get(Model, model_id).print_count == 1


def test_archive_subtask_traversal_is_sanitized(fake_adapter, seed):
    job_id = seed.job(subtask="../../etc/x")
    fake_adapter.files["/etc/x.3mf"] = b"nope"
    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "missing" and fake_adapter.downloads == []


def _commit_prints(s, job_id):
    s.expire_all()
    arc = s.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job_id))
    return arc, _run(s, job_id)


def test_archive_bad_zip_records_error_without_retry(fake_adapter, seed):
    job_id = seed.job(subtask="X_plate_1")
    fake_adapter.files["/cache/X_plate_1.3mf"] = b"not a zip"
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    arc, run = _commit_prints(seed.s, job_id)
    assert arc.fetch_state == "error" and arc.fetch_error and run is not None


def test_archive_plateless_3mf_records_error(fake_adapter, seed):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "x")
    job_id = seed.job(subtask="X_plate_1", plate=None)
    fake_adapter.files["/cache/X_plate_1.3mf"] = buf.getvalue()
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    arc, run = _commit_prints(seed.s, job_id)
    assert arc.fetch_state == "error" and run is not None


def test_archive_ftplib_error_is_retryable(fake_adapter, seed, monkeypatch):
    job_id = seed.job(subtask="X_plate_1")
    fake_adapter.files["/cache/X_plate_1.3mf"] = b"x"

    def boom(path):
        raise ftplib.error_perm("530 login failed s3cretcode")

    monkeypatch.setattr(fake_adapter, "list_dir", boom)
    with pytest.raises(ArchiveFetchError):
        archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    arc, _ = _commit_prints(seed.s, job_id)
    assert arc.fetch_state == "error"


def test_archive_redelivery_keeps_user_edits(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    run = _run(seed.s, job_id)
    run.filament_g = 10.0
    run.result = "fail"
    run.quality = "reject"
    run.failure_reason = "spaghetti"
    seed.s.commit()
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    seed.s.expire_all()
    run = _run(seed.s, job_id)
    assert (run.filament_g, run.result, run.quality, run.failure_reason) == (
        10.0,
        "fail",
        "reject",
        "spaghetti",
    )


def test_archive_est_filament_maps_ams_tray(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
    seed.s.get(PrintJob, job_id).ams_mapping = [3]
    seed.s.commit()
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.est_filament[0]["slot"] == 0 and arc.est_filament[0]["tray"] == 3


def test_archive_tray_from_filament_id(fake_adapter, two_filament_3mf_bytes, seed):
    """Filament index 0 has id 1 -> mapping[0]=3; filament index 1 has id
    3 -> mapping[2]=0 (``-1`` at index 1 is skipped entirely)."""
    job_id = seed.job(subtask="X_plate_1")
    seed.s.get(PrintJob, job_id).ams_mapping = [3, -1, 0]
    seed.s.commit()
    fake_adapter.files["/cache/X_plate_1.3mf"] = two_filament_3mf_bytes
    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    trays = [row.get("tray") for row in arc.est_filament]
    assert trays == [3, 0]


def test_archive_legacy_meta_without_filament_id_maps_tray(
    fake_adapter, legacy_meta_3mf_bytes, seed
):
    """Item 3: a plate whose single filament has no `id` attribute at all
    (pre-P5 meta) must still fall back to its 1-based plate position (1)
    so `est_filament`/the tray mapping aren't silently dropped."""
    job_id = seed.job(subtask="X_plate_1")
    seed.s.get(PrintJob, job_id).ams_mapping = [3]
    seed.s.commit()
    fake_adapter.files["/cache/X_plate_1.3mf"] = legacy_meta_3mf_bytes
    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.est_filament[0]["filament_id"] == 1
    assert arc.est_filament[0]["slot"] == 0 and arc.est_filament[0]["tray"] == 3


def test_archive_writes_usage_per_tray(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
    printer_id = seed.printer_id
    seed.s.get(PrintJob, job_id).ams_mapping = [0]
    seed.s.commit()
    spool_id = spool_on_tray(seed.s, printer_id, 0)
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    seed.s.expire_all()
    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    assert len(rows) == 1
    assert rows[0].spool_id == spool_id and rows[0].source == SpoolUsageSource.THREE_MF
    assert rows[0].grams == pytest.approx(61.64)
    spool = seed.s.get(Spool, spool_id)
    assert spool.weight_used_g == pytest.approx(61.64)


def test_archive_twice_decrements_once(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
    printer_id = seed.printer_id
    seed.s.get(PrintJob, job_id).ams_mapping = [0]
    seed.s.commit()
    spool_id = spool_on_tray(seed.s, printer_id, 0)
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    for _ in range(2):
        archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    seed.s.expire_all()
    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    assert len(rows) == 1
    spool = seed.s.get(Spool, spool_id)
    assert spool.weight_used_g == pytest.approx(61.64)


def test_archive_cost_uses_spool_rate(fake_adapter, fifty_gram_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
    printer_id = seed.printer_id
    seed.s.get(PrintJob, job_id).ams_mapping = [0]
    seed.s.commit()
    spool_on_tray(seed.s, printer_id, 0, cost_per_kg=40.0)
    fake_adapter.files["/cache/X_plate_1.3mf"] = fifty_gram_3mf_bytes
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    run = _run(seed.s, job_id)
    assert run.filament_cost == pytest.approx(2.0)


def test_archive_missing_3mf_uses_ams_delta(fake_adapter, seed):
    job_id = seed.job(subtask="Nope")
    printer_id = seed.printer_id
    spool_id = spool_on_tray(seed.s, printer_id, 0, cost_per_kg=40.0)
    job = seed.s.get(PrintJob, job_id)
    job.tray_snapshot = {"start": [100], "end": [50]}
    seed.s.commit()
    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "missing" and arc.est_filament == []
    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    assert len(rows) == 1 and rows[0].source == SpoolUsageSource.AMS_DELTA
    assert rows[0].grams == pytest.approx(500.0)  # 1000g label * 50% delta
    run = _run(seed.s, job_id)
    assert run.filament_cost == pytest.approx(500.0 / 1000 * 40.0)
    spool = seed.s.get(Spool, spool_id)
    assert spool.weight_used_g == pytest.approx(500.0)


def test_archive_wear_and_sampled_energy_price(fake_adapter, sliced_3mf_bytes, seed):
    s = seed.s
    job_id = seed.job(subtask="X_plate_1")
    s.get(Printer, seed.printer_id).wear_cost_per_hour = 0.5
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    job = s.get(PrintJob, job_id)
    job.energy_start_kwh, job.energy_end_kwh = 1.0, 3.0
    job.energy_price_start, job.energy_price_end = 0.2, 0.4
    s.commit()
    archive_job(s, get_settings(), job_id, ArchiveResult.COMPLETED)
    run = _run(s, job_id)
    assert run.wear_cost == pytest.approx(0.5)  # 1h x 0.5
    assert run.energy_cost == pytest.approx(2.0 * 0.3)


def test_reprint_appends_run_to_same_archive(fake_adapter, sliced_3mf_bytes, seed):
    s, settings = seed.s, get_settings()
    fake_adapter.files["/cache/Same_plate_1.3mf"] = sliced_3mf_bytes
    j1 = seed.job(subtask="Same_plate_1")
    a1 = archive_job(s, settings, j1, ArchiveResult.COMPLETED)
    a1.snapshot_blob = None
    a1.confirm_requested = True
    s.commit()
    j2 = seed.job(subtask="Same_plate_1")
    a2 = archive_job(s, settings, j2, ArchiveResult.FAILED)
    assert a2.id == a1.id
    assert s.scalars(select(PrintArchive)).all() == [a2]
    assert a2.run_count == 2 and a2.print_job_id == j2 and a2.result == "failed"
    assert a2.confirm_requested is False or a2.user_verdict is None
    runs = s.scalars(select(Print).order_by(Print.id)).all()
    assert [r.print_job_id for r in runs] == [j1, j2]
    assert {r.archive_id for r in runs} == {a2.id}
    assert [r.result for r in runs] == ["success", "fail"]
    # redelivery of the older job is a no-op, not a repoint
    again = archive_job(s, settings, j1, ArchiveResult.COMPLETED)
    assert again.id == a2.id and again.print_job_id == j2 and again.run_count == 2


def test_distinct_file_gets_new_archive(
    fake_adapter, sliced_3mf_bytes, two_filament_3mf_bytes, seed
):
    s, settings = seed.s, get_settings()
    fake_adapter.files["/cache/A_plate_1.3mf"] = sliced_3mf_bytes
    fake_adapter.files["/cache/B_plate_1.3mf"] = two_filament_3mf_bytes
    a = archive_job(s, settings, seed.job(subtask="A_plate_1"), ArchiveResult.COMPLETED)
    b = archive_job(s, settings, seed.job(subtask="B_plate_1"), ArchiveResult.COMPLETED)
    assert a.id != b.id and a.run_count == b.run_count == 1
    assert len(s.scalars(select(PrintArchive)).all()) == 2


def test_other_owner_gets_own_archive(fake_adapter, sliced_3mf_bytes, seed):
    s, settings = seed.s, get_settings()
    fake_adapter.files["/cache/Own_plate_1.3mf"] = sliced_3mf_bytes
    u1, u2 = seed.user("a"), seed.user("b")
    a = archive_job(
        s, settings, seed.job(subtask="Own_plate_1", user_id=u1), ArchiveResult.COMPLETED
    )
    b = archive_job(
        s, settings, seed.job(subtask="Own_plate_1", user_id=u2), ArchiveResult.COMPLETED
    )
    c = archive_job(
        s, settings, seed.job(subtask="Own_plate_1", user_id=u1), ArchiveResult.COMPLETED
    )
    assert a.id != b.id and c.id == a.id and a.run_count == 2 and b.run_count == 1
    assert a.created_by_user_id == u1


def test_different_printer_acl_gets_own_archive(fake_adapter, sliced_3mf_bytes, seed):
    from app.models import Group
    from app.models.auth import PrinterAccess

    s, settings = seed.s, get_settings()
    fake_adapter.files["/cache/Acl_plate_1.3mf"] = sliced_3mf_bytes
    j1 = seed.job(subtask="Acl_plate_1")
    a = archive_job(s, settings, j1, ArchiveResult.COMPLETED)
    p1 = s.get(Printer, s.get(PrintJob, j1).printer_id)
    p2 = Printer(
        name="P2",
        kind=p1.kind,
        host="10.0.0.6",
        serial="SN2",
        access_code_enc=p1.access_code_enc,
    )
    g = Group(name="crew")
    s.add_all([p2, g])
    s.flush()
    s.add(PrinterAccess(group_id=g.id, printer_id=p2.id))
    s.commit()
    seed.printer_id = p2.id
    b = archive_job(s, settings, seed.job(subtask="Acl_plate_1"), ArchiveResult.COMPLETED)
    assert b.id != a.id


def test_sweep_skips_folded_jobs(fake_adapter, sliced_3mf_bytes, seed, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.tasks import archive as task_mod

    s, settings = seed.s, get_settings()
    fake_adapter.files["/cache/Sw_plate_1.3mf"] = sliced_3mf_bytes
    j1 = seed.job(subtask="Sw_plate_1")
    archive_job(s, settings, j1, ArchiveResult.COMPLETED)
    j2 = seed.job(subtask="Sw_plate_1")
    archive_job(s, settings, j2, ArchiveResult.COMPLETED)
    for j in (j1, j2):
        s.get(PrintJob, j).finished_at = datetime.now(UTC) - timedelta(minutes=10)
    s.commit()
    calls = []
    monkeypatch.setattr(task_mod.archive_print, "apply_async", lambda args: calls.append(args))
    task_mod.archive_sweep.apply()
    assert calls == []


def test_reprint_keeps_first_date_and_locks_row(fake_adapter, sliced_3mf_bytes, seed):
    from sqlalchemy import event

    s, settings = seed.s, get_settings()
    fake_adapter.files["/cache/Lk_plate_1.3mf"] = sliced_3mf_bytes
    a = archive_job(s, settings, seed.job(subtask="Lk_plate_1"), ArchiveResult.COMPLETED)
    first = a.created_at
    stmts: list[str] = []
    eng = s.get_bind()

    def listener(conn, cur, stmt, *args):
        stmts.append(stmt)

    event.listen(eng, "before_cursor_execute", listener)
    try:
        b = archive_job(s, settings, seed.job(subtask="Lk_plate_1"), ArchiveResult.COMPLETED)
    finally:
        event.remove(eng, "before_cursor_execute", listener)
    assert b.id == a.id and b.created_at == first
    assert any("FOR UPDATE" in x and "print_archives" in x for x in stmts)
