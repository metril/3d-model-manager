"""Archive a finished print job (P2): locate its sliced 3MF (local library
file or the printer's FTPS storage), store the 3MF + plate thumbnail as
content-addressed blobs, and upsert the ``print_archives`` row plus the auto
``prints`` run with its cost."""

from __future__ import annotations

import ftplib
import hashlib
import tempfile
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import File, Model, Print, PrintArchive, Printer, PrintJob, Revision
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    BlobFormat,
    BlobKind,
    PrintResult,
    PrintSource,
)
from app.models.library import Blob
from app.pipeline.slicedmeta import parse_gcode_3mf
from app.printers.connection import connection_from_printer
from app.printers.gcode3mf import NotSendableError, plates_in_gcode_3mf
from app.printers.registry import build_adapter
from app.services import billing, spool_inventory, spoolman
from app.services.app_config import get_app_config_sync
from app.services.costing import CostRates, charged_grams, filament_cost, job_energy_kwh, price_run
from app.services.derivatives import fetch_blob_to_temp, publish_bytes
from app.services.slicer_intake import _find_model_by_name_ci_sync
from app.services.slicer_naming import _safe_basename, model_name_from_filename

RESULT_TO_PRINT_RESULT = {
    ArchiveResult.COMPLETED: PrintResult.SUCCESS,
    ArchiveResult.FAILED: PrintResult.FAIL,
    ArchiveResult.CANCELED: PrintResult.PARTIAL,
    ArchiveResult.STOPPED: PrintResult.PARTIAL,
}


class ArchiveFetchError(RuntimeError):
    """Transient FTPS failure; raised after the archive row is committed with
    ``fetch_state=error`` so the caller can retry."""


def archive_path(settings: Settings, sha: str, ext: str) -> Path:
    return settings.data_dir / "archives" / sha[:2] / f"{sha}.{ext}"


def _ensure_blob(session: Session, sha: str, size: int, kind: BlobKind, fmt: BlobFormat) -> None:
    if session.get(Blob, sha) is None:
        session.add(Blob(hash=sha, size=size, kind=kind, format=fmt))
        session.flush()


def _candidate_names(job: PrintJob) -> list[tuple[str, str]]:
    """Ordered (dirs, names) probes for the FTPS search."""
    probes: list[tuple[str, str]] = []
    if job.file_id is not None:
        probes.append(("/cache", f"tdmm-{job.id}.gcode.3mf"))
        return probes
    if job.subtask_name:
        try:
            name = _safe_basename(job.subtask_name)
        except ValueError:
            return probes
        for d in ("/cache", "/"):
            for n in (f"{name}.gcode.3mf", f"{name}.3mf"):
                probes.append((d, n))
    return probes


def _fetch_ftps(settings: Settings, printer: Printer, job: PrintJob, tmp: Path) -> bytes | None:
    probes = _candidate_names(job)
    if not probes:
        return None
    conn = connection_from_printer(settings, printer)
    adapter = build_adapter(printer.kind, conn)
    try:
        listings: dict[str, list[str]] = {}
        for d, n in probes:
            if d not in listings:
                listings[d] = adapter.list_dir(d)
            if n in listings[d]:
                dest = tmp / "remote.gcode.3mf"
                remote = f"{d.rstrip('/')}/{n}"
                try:
                    adapter.download_file(remote, dest)
                except FileNotFoundError:
                    continue
                return dest.read_bytes()
        return None
    except (OSError, ftplib.Error, NotImplementedError) as e:
        raise OSError(str(e).replace(conn.access_code, "***") or type(e).__name__) from e
    finally:
        adapter.close()


def _locate(
    session: Session, settings: Settings, printer: Printer, job: PrintJob, tmp: Path
) -> bytes | None:
    if job.file_id is not None:
        f = session.get(File, job.file_id)
        if f is not None:
            try:
                p = fetch_blob_to_temp(session, settings, f.blob_hash, tmp, ".gcode.3mf")
                return p.read_bytes()
            except LookupError:
                pass
    return _fetch_ftps(settings, printer, job, tmp)


def _resolve_model(
    session: Session, job: PrintJob, sha: str | None
) -> tuple[int | None, int | None]:
    file: File | None = None
    if job.file_id is not None:
        file = session.get(File, job.file_id)
    elif sha is not None:
        file = session.scalars(
            select(File).where(File.blob_hash == sha).order_by(File.id).limit(1)
        ).first()
    if file is not None:
        rev = session.get(Revision, file.revision_id)
        if rev is not None:
            return rev.model_id, rev.id
    if job.file_id is None and job.subtask_name:
        try:
            name = model_name_from_filename(job.subtask_name)
        except ValueError:
            return None, None
        if name:
            model = _find_model_by_name_ci_sync(session, name)
            if model is not None:
                return model.id, None
    return None, None


def archive_job(
    session: Session, settings: Settings, print_job_id: int, result: ArchiveResult
) -> PrintArchive:
    job = session.get(PrintJob, print_job_id)
    if job is None:
        raise LookupError(f"print job {print_job_id} not found")
    printer = session.get(Printer, job.printer_id)
    if printer is None:
        raise LookupError(f"printer {job.printer_id} not found")

    arc = session.scalars(select(PrintArchive).where(PrintArchive.print_job_id == job.id)).first()
    if arc is None:
        arc = PrintArchive(
            print_job_id=job.id, result=result, fetch_state=ArchiveFetchState.PENDING
        )
        session.add(arc)
    arc.result = result
    arc.created_by_user_id = job.created_by_user_id

    data: bytes | None = None
    fetch_exc: OSError | None = None
    with tempfile.TemporaryDirectory(prefix="tdmm-arc-") as td:
        try:
            data = _locate(session, settings, printer, job, Path(td))
        except OSError as e:
            fetch_exc = e

    est: list[dict] = []
    sha: str | None = None
    plate_no = job.plate
    if fetch_exc is not None:
        arc.fetch_state = ArchiveFetchState.ERROR
        arc.fetch_error = str(fetch_exc)
    elif data is None:
        arc.fetch_state = ArchiveFetchState.MISSING
        arc.fetch_error = None
    else:
        sha = hashlib.sha256(data).hexdigest()
        try:
            with tempfile.TemporaryDirectory(prefix="tdmm-arc-") as td:
                p = Path(td) / "a.gcode.3mf"
                p.write_bytes(data)
                meta = parse_gcode_3mf(p)
            if plate_no is None:
                plate_no = plates_in_gcode_3mf(data)[0]
        except (zipfile.BadZipFile, NotSendableError) as e:
            # Permanent: the file will never parse, so record and don't retry.
            sha = None
            arc.fetch_state = ArchiveFetchState.ERROR
            arc.fetch_error = f"unreadable 3MF: {e}"[:500]
        else:
            publish_bytes(data, archive_path(settings, sha, "gcode.3mf"))
            _ensure_blob(session, sha, len(data), BlobKind.SLICED, BlobFormat.GCODE_3MF)
            arc.archive_blob = sha
            plate = next((x for x in meta.plates if x["index"] == plate_no), None)
            if plate is not None:
                mapping = job.ams_mapping
                for i, f in enumerate(plate["filaments"]):
                    # Pre-P5 meta parsed before `filament_id` existed carries
                    # none -- fall back to the 1-based plate position, same
                    # convention `print_plan`'s `needs`/`by_filament_id` use.
                    fid = f["filament_id"] if f.get("filament_id") is not None else i + 1
                    row = {
                        "slot": i,
                        "filament_id": fid,
                        "type": f["type"],
                        "color": f["color"],
                        "grams": f["used_g"],
                        "meters": f["used_m"],
                    }
                    tray_val: int | None = None
                    if isinstance(mapping, list):
                        if 0 <= fid - 1 < len(mapping):
                            tray_val = mapping[fid - 1]
                        elif i < len(mapping):
                            tray_val = mapping[i]
                    if tray_val is not None and tray_val != -1:
                        row["tray"] = tray_val
                    est.append(row)
                thumb = plate.get("thumbnail_file")
                if thumb:
                    with zipfile.ZipFile(BytesIO(data)) as zf:
                        if thumb in zf.namelist():
                            tb = zf.read(thumb)
                            tsha = hashlib.sha256(tb).hexdigest()
                            publish_bytes(tb, archive_path(settings, tsha, "png"))
                            _ensure_blob(session, tsha, len(tb), BlobKind.IMAGE, BlobFormat.PNG)
                            arc.thumbnail_blob = tsha
            meta_dict = asdict(meta)
            meta_dict.pop("plates", None)
            arc.slicer_meta = meta_dict
            arc.fetch_state = ArchiveFetchState.DONE
            arc.fetch_error = None

    arc.plate = plate_no
    arc.est_filament = est
    model_id, revision_id = _resolve_model(session, job, sha)
    arc.model_id = model_id
    arc.revision_id = revision_id
    duration_s: int | None = None
    if job.started_at and job.finished_at:
        duration_s = int((job.finished_at - job.started_at).total_seconds())
    arc.duration_s = duration_s

    cfg = get_app_config_sync(session, settings)
    usages = spool_inventory.record_job_usage(session, job, est, result)

    grams = [e["grams"] for e in est if e["grams"] is not None]
    est_g = sum(grams) if grams else None
    filament_g = charged_grams(
        est_g,
        result,
        layer=job.layer,
        total_layers=job.total_layers,
        progress_pct=job.progress_pct,
    )
    types = list(dict.fromkeys(e["type"] for e in est if e["type"]))
    run = session.scalars(select(Print).where(Print.print_job_id == job.id)).first()
    inserting = run is None
    if run is None:
        run = Print(print_job_id=job.id, source=PrintSource.AUTO)
        session.add(run)
    # Redelivery must not clobber user edits: only write the auto fields when
    # inserting or while they still hold their auto defaults.
    if inserting:
        run.result = RESULT_TO_PRINT_RESULT[result]
    if inserting or run.filament_g is None:
        run.filament_g = filament_g
    if inserting or run.energy_kwh is None:
        run.energy_kwh = job_energy_kwh(job.energy_start_kwh, job.energy_end_kwh)
    energy_cost = (
        None if run.energy_kwh is None else round(run.energy_kwh * cfg.energy_cost_per_kwh, 4)
    )
    cost = price_run(
        filament_g,
        duration_s,
        CostRates.from_config(cfg),
        energy_cost=energy_cost,
        filament_cost=filament_cost(usages, cfg.filament_cost_per_kg),
    )
    run.model_id = model_id
    run.created_by_user_id = job.created_by_user_id
    run.printer_name = printer.name
    run.filament = ", ".join(types) or None
    run.duration_min = round(duration_s / 60) if duration_s is not None else None
    run.printed_at = job.finished_at or datetime.now(UTC)
    run.filament_cost = cost.filament_cost
    run.machine_cost = cost.machine_cost
    run.energy_cost = cost.energy_cost
    run.total_cost = cost.total_cost
    if inserting and model_id is not None:
        model = session.get(Model, model_id)
        if model is not None:
            model.print_count += 1
    session.flush()  # so `run.id` exists for `settle_job`'s `charge(print_id=run.id)`
    billing.settle_job(session, job, run)
    session.commit()
    # Q9: after the commit so a rolled-back/redelivered archive can't double-push.
    spoolman.push_usages(session, usages)
    if fetch_exc is not None:
        raise ArchiveFetchError(str(fetch_exc)) from fetch_exc
    return arc


def mark_archive_error(
    session: Session, print_job_id: int, result: ArchiveResult, message: str
) -> None:
    """Record a non-retried failure (e.g. task time limit) on the archive row."""
    session.rollback()
    arc = session.scalars(
        select(PrintArchive).where(PrintArchive.print_job_id == print_job_id)
    ).first()
    if arc is None:
        arc = PrintArchive(print_job_id=print_job_id, result=result)
        session.add(arc)
    arc.fetch_state = ArchiveFetchState.ERROR
    arc.fetch_error = message
    session.commit()
