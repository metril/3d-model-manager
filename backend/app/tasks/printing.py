"""Send-to-printer flow (SPEC "Printer integration"; RESEARCH §4). io queue.
Validate .gcode.3mf -> preflight state ∈ {IDLE,FINISH,FAILED} -> FTPS upload
-> project_file. Decrypts the access code HERE (worker) only; every failure
marks the print_jobs row FAILED with an actionable message.

Both CRITICAL invariants are re-checked HERE, authoritatively, regardless of
what the API's fast pre-checks already found (closing the TOCTOU between the
API request and this task actually running):

- bare-``.gcode``/no-plate-gcode reject: ``gcode3mf.assert_plate_available``
  opens the REAL archive bytes fetched from storage and requires the
  requested plate's ``Metadata/plate_N.gcode`` to be physically present --
  it never trusts the blob's ``format`` label.
- preflight gate: ``read_state_sync``/``preflight_ok`` are re-read at the
  moment of sending, not carried over from the API's own read.

In both cases the failure happens BEFORE ``build_adapter``/
``upload_and_start`` are ever reached, so a rejected job never touches the
printer.
"""

from __future__ import annotations

import contextlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import redis

from app.config import Settings, get_settings
from app.models import Blob, File, Printer, PrintJob
from app.models.enums import PrintJobState
from app.printers import gcode3mf
from app.printers.base import PrinterConnection, PrintSpec
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.services import derivatives, kprofiles
from app.services.events import publish_print_job_event_sync
from app.services.printer_state import preflight_ok, read_state_sync
from app.tasks import base
from app.tasks.celery_app import celery_app


class SendError(RuntimeError):
    """Actionable send-flow failure surfaced on the print_jobs row."""


def _scrub(exc: Exception, conn: PrinterConnection | None) -> str:
    """``job.printer_error`` is exposed verbatim over the API (``PrintJobOut``)
    -- an adapter exception (bambulabs_api/ftplib/paho) could echo the
    plaintext access code back in its message (e.g. an FTPS auth-failure
    string). Defensively redact any occurrence of the decrypted code before
    it's ever persisted, without touching the rest of the actionable detail.
    ``conn`` is ``None`` when the failure happened while decrypting the
    access code itself (see ``connection_from_printer`` below) -- there is no
    plaintext code to redact in that case.
    """
    message = str(exc)
    if conn is not None and conn.access_code and conn.access_code in message:
        return message.replace(conn.access_code, "***")
    return message


def _set_job_state(
    settings: Settings, print_job_id: int, state: PrintJobState, *, error: str | None = None
) -> None:
    with base.sync_session() as s:
        job = s.get(PrintJob, print_job_id)
        if job is None:
            return
        job.state = state.value
        if error is not None:
            job.printer_error = error
        if state in (PrintJobState.FAILED, PrintJobState.CANCELED):
            job.finished_at = datetime.now(UTC)
        printer_id = job.printer_id
        s.commit()
    publish_print_job_event_sync(
        settings.redis_url, print_job_id=print_job_id, printer_id=printer_id, state=state.value
    )


@celery_app.task(name="app.tasks.printing.send_to_printer")
def send_to_printer(print_job_id: int, options: dict) -> None:
    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url)
    try:
        _send_to_printer(settings, client, print_job_id, options)
    finally:
        client.close()


def _send_to_printer(
    settings: Settings, client: redis.Redis, print_job_id: int, options: dict
) -> None:
    with base.sync_session() as s:
        job = s.get(PrintJob, print_job_id)
        if job is None:
            return
        printer = s.get(Printer, job.printer_id)
        file = s.get(File, job.file_id)
        if printer is None or file is None:
            _set_job_state(
                settings, print_job_id, PrintJobState.FAILED, error="printer or file missing"
            )
            return
        blob = s.get(Blob, file.blob_hash)
        printer_id, kind = printer.id, printer.kind
        blob_hash = blob.hash
        subtask = job.subtask_name or file.rel_path
    _set_job_state(settings, print_job_id, PrintJobState.UPLOADING)
    conn: PrinterConnection | None = None
    try:
        # Decrypt HERE, inside the try: a rotated/corrupted Fernet key raises
        # cryptography.fernet.InvalidToken, which must mark the job FAILED
        # like every other send-flow failure rather than escape and leave the
        # row stuck in QUEUED/UPLOADING forever. `conn` stays None on failure
        # so `_scrub` below has nothing to redact (there is no plaintext code
        # to leak -- decrypting it is exactly what failed).
        try:
            conn = connection_from_printer(settings, printer)  # decrypt (worker)
        except Exception as e:
            raise SendError(
                "printer access code could not be decrypted (was the printer key rotated?)"
            ) from e
        state = read_state_sync(client, printer_id)
        if not preflight_ok(state):
            gs = state.get("gcode_state") if state else "unknown"
            raise SendError(
                f"printer not ready (state={gs}); must be IDLE/FINISH/FAILED. "
                "Is printerd running and the printer online and idle?"
            )
        with tempfile.TemporaryDirectory() as tmp, base.sync_session() as s2:
            # Workstream C task C2: resolves the specific verified File's own
            # backend internally (a blob can have verified copies on more
            # than one backend) rather than a caller-supplied default.
            path = derivatives.fetch_blob_to_temp(s2, settings, blob_hash, Path(tmp), ".gcode.3mf")
            try:
                gcode3mf.assert_plate_available(path.read_bytes(), options["plate"])
            except gcode3mf.NotSendableError as e:
                raise SendError(str(e)) from e
            spec = PrintSpec(
                source_path=path,
                remote_name=f"tdmm-{print_job_id}.gcode.3mf",
                plate=options["plate"],
                subtask_name=subtask,
                use_ams=options["use_ams"],
                ams_mapping=tuple(options["ams_mapping"]),
                bed_levelling=options["bed_levelling"],
                flow_cali=options["flow_cali"],
                timelapse=options["timelapse"],
            )
            # T2: put a lost K-profile selection back before the job starts.
            if kprofiles.has_lost_trays((state or {}).get("trays") or []):
                kprofiles.request_reapply_sync(client, printer_id)
            adapter = build_adapter(kind, conn)
            try:
                adapter.upload_and_start(spec)
            finally:
                with contextlib.suppress(Exception):
                    adapter.close()
        _set_job_state(settings, print_job_id, PrintJobState.STARTING)
    except Exception as exc:
        _set_job_state(settings, print_job_id, PrintJobState.FAILED, error=_scrub(exc, conn))
        raise
