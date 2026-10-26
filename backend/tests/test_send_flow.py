import json
from datetime import UTC, datetime

import pytest
import redis as redis_lib
from blake3 import blake3
from sqlalchemy import select

from app.config import get_settings
from app.models import Blob, File, Model, Printer, PrintJob, Revision
from app.models.enums import BlobFormat, BlobKind, PrinterKind, PrintJobState
from app.printers.base import state_key
from app.storage.local import LocalStorageBackend
from app.tasks import base
from app.tasks.printing import SendError, send_to_printer
from tests import corpus

CREATE = {"name": "A1 mini", "host": "192.168.1.50", "serial": "0309ABC", "access_code": "12345678"}
DEFAULT_OPTS = {
    "plate": 1,
    "use_ams": False,
    "ams_mapping": [0],
    "bed_levelling": True,
    "flow_cali": True,
    "timelapse": False,
}


def _seed_state(redis_url: str, printer_id: int, gcode_state: str | None) -> None:
    c = redis_lib.Redis.from_url(redis_url)
    if gcode_state is not None:
        c.set(state_key(printer_id), json.dumps({"gcode_state": gcode_state}))
    c.close()


async def _sliced_file(
    db_session, seed_file, *, fmt=BlobFormat.GCODE_3MF, kind=BlobKind.SLICED, content=None
) -> File:
    content = content if content is not None else corpus.sliced_gcode_3mf()
    model = Model(slug="sendm", name="Send M")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(rev)
    await db_session.commit()
    await db_session.refresh(rev)
    return await seed_file(model, rev, "job.gcode.3mf", content, blob_format=fmt, blob_kind=kind)


async def _make_printer(client) -> int:
    return (await client.post("/api/printers", json=CREATE)).json()["id"]


# ---------------------------------------------------------------------------
# API layer: fast pre-checks (Invariant 1 fast-reject, Invariant 2 fast gate)
# ---------------------------------------------------------------------------


async def test_bare_gcode_rejected_422(
    authenticated_client, printer_enabled, library_root, seed_file, db_session
):
    pid = await _make_printer(authenticated_client)
    f = await _sliced_file(
        db_session,
        seed_file,
        fmt=BlobFormat.GCODE,
        kind=BlobKind.GCODE,
        content=corpus.bambu_gcode(),
    )
    r = await authenticated_client.post(f"/api/printers/{pid}/print", json={"file_id": f.id})
    assert r.status_code == 422


async def test_disabled_printer_409(
    authenticated_client, printer_enabled, library_root, seed_file, db_session, redis_url
):
    """Fix 1: the per-printer ``enabled`` column (distinct from the global
    PRINTER_ENABLED flag) must gate sends -- printerd only reads
    ``enabled_printers()`` once at startup, so the API check is the real
    protection against a printer disabled mid-run."""
    pid = await _make_printer(authenticated_client)
    printer = await db_session.get(Printer, pid)
    printer.enabled = False
    await db_session.commit()
    f = await _sliced_file(db_session, seed_file)
    _seed_state(redis_url, pid, "IDLE")  # even if otherwise ready...
    r = await authenticated_client.post(f"/api/printers/{pid}/print", json={"file_id": f.id})
    assert r.status_code == 409
    assert r.json()["detail"] == "printer is disabled"
    assert (await db_session.execute(select(PrintJob))).scalars().first() is None


async def test_corrupt_redis_state_fails_closed_409(
    authenticated_client, printer_enabled, library_root, seed_file, db_session, redis_url
):
    """Fix 2: a corrupt/partial Redis value at state_key(id) must not 500 the
    preflight read -- it degrades to "not ready" (409), same as an absent
    key."""
    pid = await _make_printer(authenticated_client)
    f = await _sliced_file(db_session, seed_file)
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), "not json")
    c.close()
    r = await authenticated_client.post(f"/api/printers/{pid}/print", json={"file_id": f.id})
    assert r.status_code == 409


async def test_busy_printer_409(
    authenticated_client, printer_enabled, library_root, seed_file, db_session, redis_url
):
    pid = await _make_printer(authenticated_client)
    f = await _sliced_file(db_session, seed_file)
    _seed_state(redis_url, pid, "RUNNING")
    r = await authenticated_client.post(f"/api/printers/{pid}/print", json={"file_id": f.id})
    assert r.status_code == 409


async def test_unknown_state_409(
    authenticated_client, printer_enabled, library_root, seed_file, db_session, redis_url
):
    pid = await _make_printer(authenticated_client)
    f = await _sliced_file(db_session, seed_file)
    _seed_state(redis_url, pid, None)  # nothing in Redis -> cannot confirm idle
    r = await authenticated_client.post(f"/api/printers/{pid}/print", json={"file_id": f.id})
    assert r.status_code == 409


@pytest.mark.parametrize("bad_state", ["RUNNING", "PAUSE", "PREPARE", "UNKNOWN"])
async def test_every_disallowed_state_409(
    authenticated_client, printer_enabled, library_root, seed_file, db_session, redis_url, bad_state
):
    """Adversarial: the allowed set is {IDLE, FINISH, FAILED} -- prove every
    other real gcode_state token (not just RUNNING) is rejected, so the gate
    isn't accidentally hardcoded to a single disallowed value."""
    pid = await _make_printer(authenticated_client)
    f = await _sliced_file(db_session, seed_file)
    _seed_state(redis_url, pid, bad_state)
    r = await authenticated_client.post(f"/api/printers/{pid}/print", json={"file_id": f.id})
    assert r.status_code == 409


async def test_send_success_uploads_and_starts(
    authenticated_client,
    printer_enabled,
    library_root,
    seed_file,
    db_session,
    redis_url,
    fake_adapter,
):
    pid = await _make_printer(authenticated_client)
    f = await _sliced_file(db_session, seed_file)
    _seed_state(redis_url, pid, "IDLE")
    r = await authenticated_client.post(
        f"/api/printers/{pid}/print", json={"file_id": f.id, "plate": 1}
    )
    assert r.status_code == 201, r.text
    assert len(fake_adapter.uploaded) == 1  # eager task ran the upload
    spec = fake_adapter.uploaded[0]
    assert spec.plate == 1 and spec.remote_name.endswith(".gcode.3mf")
    job = await db_session.get(PrintJob, r.json()["id"])
    assert job.state == PrintJobState.STARTING.value


# ---------------------------------------------------------------------------
# Task layer: authoritative re-checks (the TOCTOU-closing gate). Seeded
# directly via the sync world, bypassing the API's fast pre-check entirely,
# to prove the task itself refuses -- not just the endpoint in front of it.
# ---------------------------------------------------------------------------


def _seed_printer_and_job(settings, redis_url, *, content, fmt) -> tuple[int, int]:
    backend = LocalStorageBackend(settings.library_root)
    digest = blake3(content).hexdigest()
    with base.sync_session() as s:
        printer = Printer(
            name="p",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="S",
            access_code_enc=_enc(settings),
            enabled=True,
        )
        blob = Blob(hash=digest, size=len(content), kind=BlobKind.SLICED, format=fmt)
        model = Model(slug="td", name="TD")
        s.add_all([printer, blob, model])
        s.flush()
        rev = Revision(model_id=model.id, number=1, dir_name="rev-001")
        s.add(rev)
        s.flush()
        path = "td/rev-001/job.gcode.3mf"
        backend.write(path, [content])
        f = File(
            revision_id=rev.id,
            blob_hash=digest,
            rel_path="job.gcode.3mf",
            storage_path=path,
            verified_at=datetime.now(UTC),
        )
        s.add(f)
        s.flush()
        job = PrintJob(
            printer_id=printer.id, file_id=f.id, state=PrintJobState.QUEUED, subtask_name="w"
        )
        s.add(job)
        s.commit()
        return printer.id, job.id


def _enc(settings) -> str:
    from app.crypto import encrypt_secret

    return encrypt_secret(settings, "12345678")


def test_task_preflight_failure_marks_failed(
    printer_enabled, library_root, redis_url, fake_adapter
):
    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    _seed_state(redis_url, pid, "RUNNING")  # not idle
    with pytest.raises(SendError):
        send_to_printer(job_id, DEFAULT_OPTS)
    with base.sync_session() as s:
        assert s.get(PrintJob, job_id).state == PrintJobState.FAILED.value
    assert fake_adapter.uploaded == []  # never touched the printer


def test_task_absent_state_marks_failed(printer_enabled, library_root, redis_url, fake_adapter):
    """Invariant 2, task layer: printerd never having reported (no key in
    Redis at all) must fail closed, exactly like a known-busy state --
    proven at the task's authoritative re-check, not just the API's
    pre-check (test_unknown_state_409 above only proves the latter)."""
    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    # deliberately do not seed any state for this printer
    with pytest.raises(SendError):
        send_to_printer(job_id, DEFAULT_OPTS)
    with base.sync_session() as s:
        assert s.get(PrintJob, job_id).state == PrintJobState.FAILED.value
    assert fake_adapter.uploaded == []


def test_task_bare_gcode_refused_never_uploads(
    printer_enabled, library_root, redis_url, fake_adapter
):
    """Invariant 1, task layer: even with the printer IDLE (preflight would
    pass) and the API's fast .gcode.3mf check bypassed entirely, a bare
    .gcode payload must never reach upload_and_start -- the task's own
    archive-open is the authoritative gate, not the blob's format label."""
    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.bambu_gcode(), fmt=BlobFormat.GCODE
    )
    _seed_state(redis_url, pid, "IDLE")
    with pytest.raises(SendError):
        send_to_printer(job_id, DEFAULT_OPTS)
    with base.sync_session() as s:
        assert s.get(PrintJob, job_id).state == PrintJobState.FAILED.value
    assert fake_adapter.uploaded == []


def test_task_no_plate_gcode_at_all_marks_failed(
    printer_enabled, library_root, redis_url, fake_adapter
):
    """Invariant 1, task layer, distinct from "plate 2 unavailable" below: a
    .gcode.3mf archive with ZERO Metadata/plate_N.gcode entries at all
    (box_3mf_bambu -- no Metadata/ directory whatsoever) must fail, not just
    one requesting an out-of-range plate number."""
    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.box_3mf_bambu(), fmt=BlobFormat.GCODE_3MF
    )
    _seed_state(redis_url, pid, "IDLE")
    with pytest.raises(SendError):
        send_to_printer(job_id, DEFAULT_OPTS)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FAILED.value
        assert "no Metadata/plate" in (job.printer_error or "")
    assert fake_adapter.uploaded == []


def test_task_plate_unavailable_marks_failed(
    printer_enabled, library_root, redis_url, fake_adapter
):
    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    _seed_state(redis_url, pid, "IDLE")
    with pytest.raises(SendError):
        send_to_printer(job_id, {**DEFAULT_OPTS, "plate": 2})  # plate 2 gcode absent
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FAILED.value and "plate 2" in (job.printer_error or "")
    assert fake_adapter.uploaded == []


@pytest.mark.parametrize("good_state", ["IDLE", "FINISH", "FAILED"])
def test_task_every_allowed_state_proceeds(
    printer_enabled, library_root, redis_url, fake_adapter, good_state
):
    """Invariant 2, full allowed-set coverage: IDLE, FINISH, AND FAILED must
    all let a valid send proceed -- not just IDLE, which is the only value
    the happy-path API test above exercises."""
    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    _seed_state(redis_url, pid, good_state)
    send_to_printer(job_id, DEFAULT_OPTS)
    with base.sync_session() as s:
        assert s.get(PrintJob, job_id).state == PrintJobState.STARTING.value
    assert len(fake_adapter.uploaded) == 1
    spec = fake_adapter.uploaded[0]
    assert spec.plate == 1
    assert spec.remote_name.endswith(".gcode.3mf")
    assert spec.use_ams is False
    assert spec.ams_mapping == (0,)


def test_task_decrypt_failure_marks_failed_never_uploads(
    printer_enabled, library_root, redis_url, fake_adapter
):
    """M4 review Fix B: if the Fernet key was rotated (or ``access_code_enc``
    is otherwise undecryptable), ``connection_from_printer`` raises
    ``cryptography.fernet.InvalidToken``. That decrypt now happens INSIDE the
    task's try/except, so the job must land FAILED (never stuck QUEUED),
    upload_and_start must never be invoked, and -- since there is no
    plaintext code to leak in the first place -- the failure text must not
    contain it either."""
    from cryptography.fernet import Fernet

    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    _seed_state(redis_url, pid, "IDLE")
    # A well-formed Fernet token encrypted under an unrelated key: syntactically
    # valid, but fails HMAC verification under the real settings key -- exactly
    # what a rotated/lost printer key produces in production.
    bogus = Fernet(Fernet.generate_key()).encrypt(b"12345678").decode()
    with base.sync_session() as s:
        printer = s.get(Printer, pid)
        printer.access_code_enc = bogus
        s.commit()

    with pytest.raises(SendError):
        send_to_printer(job_id, DEFAULT_OPTS)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FAILED.value
        assert job.state != PrintJobState.QUEUED.value
        assert "12345678" not in (job.printer_error or "")
    assert fake_adapter.uploaded == []  # never touched the printer


def test_task_scrubs_access_code_from_failure_message(
    printer_enabled, library_root, redis_url, fake_adapter
):
    """Fix 3: whatever the adapter's exception says (bambulabs_api/ftplib/
    paho all surface raw provider strings), the plaintext access code -- here
    "12345678", the same code ``_seed_printer_and_job``/``_enc`` encrypt onto
    the printer row -- must never survive into ``job.printer_error``, since
    ``PrintJobOut`` exposes that field over the API verbatim."""
    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    _seed_state(redis_url, pid, "IDLE")

    def _raise(spec):
        raise RuntimeError("ftps auth failed for access code 12345678")

    fake_adapter.upload_and_start = _raise
    with pytest.raises(RuntimeError):
        send_to_printer(job_id, DEFAULT_OPTS)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FAILED.value
        assert "12345678" not in (job.printer_error or "")
        assert "***" in (job.printer_error or "")
        assert "ftps auth failed" in (job.printer_error or "")


def _trash_model_of_job(job_id: int) -> int:
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        f = s.get(File, job.file_id)
        rev = s.get(Revision, f.revision_id)
        s.get(Model, rev.model_id).deleted_at = datetime.now(UTC)
        s.commit()
        return f.id


def test_task_refuses_trashed_model_and_holds_entry(
    printer_enabled, library_root, redis_url, fake_adapter
):
    from app.models import PrintQueueEntry
    from app.models.enums import QueueState

    settings = get_settings()
    pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    _seed_state(redis_url, pid, "IDLE")
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        f = s.get(File, job.file_id)
        model_id = s.get(Revision, f.revision_id).model_id
        entry = PrintQueueEntry(
            model_id=model_id,
            file_id=f.id,
            sliced_file_id=f.id,
            position=1,
            copies=1,
            state=QueueState.READY,
            print_job_id=job_id,
        )
        s.add(entry)
        s.commit()
        entry_id = entry.id
    _trash_model_of_job(job_id)

    send_to_printer(job_id, DEFAULT_OPTS)  # refuses quietly (job FAILED), never raises

    assert fake_adapter.uploaded == []
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FAILED.value and job.printer_error == "model in trash"
        e = s.get(PrintQueueEntry, entry_id)
        assert e.state == QueueState.HELD and e.last_error == "model in trash"


def test_vp_enqueue_skips_trashed_model(printer_enabled, library_root, redis_url):
    from types import SimpleNamespace

    from app.models import PrintQueueEntry
    from app.models.enums import VirtualPrinterMode
    from app.virtual_printer import intake

    settings = get_settings()
    _pid, job_id = _seed_printer_and_job(
        settings, redis_url, content=corpus.sliced_gcode_3mf(), fmt=BlobFormat.GCODE_3MF
    )
    fid = _trash_model_of_job(job_id)
    cfg = SimpleNamespace(
        mode=VirtualPrinterMode.REVIEW, target_printer_id=None, name="vp", created_by_user_id=None
    )
    with base.sync_session() as s:
        model_id = s.get(Revision, s.get(File, fid).revision_id).model_id
        assert intake._enqueue(s, settings, cfg, model_id, fid, {}) is None
        assert s.scalar(select(PrintQueueEntry.id)) is None
