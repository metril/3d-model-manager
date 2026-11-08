"""``slice_file`` Celery task (P6 "slicer, profiles, recipes and dispatch"
plan, Task 5): slices a queued print's source file through the sidecar and
stores the result as a new ``File`` on the source file's revision.

CPU queue (``app.tasks.celery_app``'s ``task_routes`` -- slicing is CPU-heavy
like the pipeline's extraction/render steps). Runs entirely in the worker's
SYNC world (``app.tasks.base``), same reasoning as every other task module.

Retry rule: a validation/slicer-side failure (``SlicerError``, or a
``ValueError`` this module raises itself for a bad plate/printer-model
match) consumes one of the entry's ``attempts`` -- ``waiting`` again below
``MAX_ATTEMPTS``, ``failed`` once it's reached. ``SlicerUnavailable`` (the
sidecar couldn't be reached at all) does NOT consume an attempt -- the
dispatcher presumably retries once the sidecar is back, same as it would
have tried this very attempt again.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from blake3 import blake3
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.importers.download import StagedFile
from app.models import PrintRecipe, SliceJob, SlicerProfile
from app.models.enums import BlobFormat, BlobKind, QueueState
from app.models.library import Blob, File, Model, PrintQueueEntry, Revision
from app.pipeline import plates3mf, slicedmeta
from app.services import billing, derivatives, events, library, spool
from app.services import file_settings as file_settings_service
from app.services.app_config import get_app_config_sync
from app.services.dispatcher import (
    ALL_PLATES_KEY,
    FILE_SETTINGS_KEY,
    QUEUE_MAX_ATTEMPTS,
    slice_siblings,
)
from app.services.print_plan import estimate_item_sync
from app.services.profile_sanitize import (
    KNOWN_RANGES,
    clamp_key,
    parse_range_error,
    sanitize_profile,
)
from app.services.server_config import SLICE_TIMEOUT_MAX_S, get_server_config_sync
from app.services.slicer_client import (
    SliceOptions,
    SlicerBusy,
    SlicerCancelled,
    SlicerClient,
    SlicerError,
    SlicerUnavailable,
    client_from_config,
)
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Shared with the dispatcher's own attempt cap -- see module docstring
# ("slicing + dispatch share the one `attempts` counter").
MAX_ATTEMPTS = QUEUE_MAX_ATTEMPTS


def queue_job_id(entry_id: int) -> str:
    """The sidecar job id of a queue entry's slice (see `DELETE /jobs/{id}`)."""
    return f"q{entry_id}"


def slice_job_sidecar_id(job_id: uuid.UUID | str) -> str:
    return f"sj-{job_id}"


def _notify(event: str, payload: dict) -> None:
    from app.services.notify.events import emit

    emit(event, None, payload)


def _publish(settings, **kwargs) -> None:
    events.publish_slice_event_sync(settings.redis_url, **kwargs)


def _lock_claimed(session: Session, entry_id: int, claimed_at) -> PrintQueueEntry | None:
    """Re-reads the entry under a row lock and returns it only if it's still
    the very `slicing` attempt we claimed (else None: the result is stale)."""
    current = session.execute(
        select(PrintQueueEntry)
        .where(PrintQueueEntry.id == entry_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        current is None
        or current.state != QueueState.SLICING
        or current.slicing_started_at != claimed_at
    ):
        session.rollback()
        return None
    return current


def _discard_new_file(session: Session, settings, outcome: SliceOutcome | None) -> None:
    """Best-effort removal of a sliced File this run just created but whose
    owner (job/entry) was cancelled or deleted meanwhile. Reused existing
    files (``created`` False) are never touched; a file whose store job is
    still in flight is left alone (deleting it would race the ingest)."""
    if outcome is None or not outcome.created:
        return
    try:
        file = session.get(File, outcome.file_id)
        if file is None or library.file_store_pending_sync(session, file):
            return
        from contextlib import suppress

        from app.services.storage_config import resolve_backend_sync

        blob_hash, key = file.blob_hash, file.storage_path
        session.delete(file)
        session.flush()
        shared = session.scalar(select(File.id).where(File.blob_hash == blob_hash).limit(1))
        if shared is None:
            blob = session.get(Blob, blob_hash)
            if blob is not None:
                session.delete(blob)
        session.commit()
        with suppress(Exception):
            resolve_backend_sync(session, settings).delete(key)
    except Exception:  # noqa: BLE001 -- cleanup must never mask the real outcome
        logger.warning("could not discard orphan sliced file %s", outcome.file_id, exc_info=True)
        session.rollback()


# The slice HTTP call may run up to the admin-configurable timeout maximum
# (+ the slicer client's grace); the task limits must outlast it or a
# legitimately long slice is hard-killed. The stuck-job sweep reaps any row a
# kill still leaves behind.
SLICE_TASK_SOFT_LIMIT_S = SLICE_TIMEOUT_MAX_S + 600
SLICE_TASK_HARD_LIMIT_S = SLICE_TASK_SOFT_LIMIT_S + 300


@celery_app.task(
    name="app.tasks.slicing.slice_file",
    bind=True,
    soft_time_limit=SLICE_TASK_SOFT_LIMIT_S,
    time_limit=SLICE_TASK_HARD_LIMIT_S,
)
def slice_file(self, queue_entry_id: int) -> None:
    """Claims the row in a short transaction (CAS: still `slicing`, stamp
    `slicing_started_at`), commits to release the row lock, then runs the
    HTTP slice with NO lock held -- a long-running slice must never block
    `app.services.queue.move_entry`/other writers touching the same row.
    The result is written back in a final, separately-fetched (unlocked)
    transaction.
    """
    settings = get_settings()
    with base.sync_session() as session:
        entry = session.execute(
            select(PrintQueueEntry).where(PrintQueueEntry.id == queue_entry_id).with_for_update()
        ).scalar_one_or_none()
        if entry is None or entry.state != QueueState.SLICING:
            return
        entry.slicing_started_at = datetime.now(UTC)
        entry.slice_task_id = getattr(self.request, "id", None)
        entry.last_log = None
        session.commit()

    with base.sync_session() as session, contextlib.ExitStack() as _closer:
        cfg = get_app_config_sync(session, settings)
        server_cfg = get_server_config_sync(session, settings)
        client = client_from_config(server_cfg)
        if client is not None:
            _closer.callback(client.close)
        entry = session.get(PrintQueueEntry, queue_entry_id)
        if entry is None or entry.state != QueueState.SLICING:
            return
        claimed_at = entry.slicing_started_at

        if client is None:
            entry.last_error = (
                "slicer_disabled"
                if not server_cfg.slicer_enabled
                else "slicer sidecar not configured"
            )
            entry.state = QueueState.WAITING
            entry.slicing_started_at = None
            session.commit()
            return

        def _still_ours() -> bool:
            row = session.execute(
                select(PrintQueueEntry.state, PrintQueueEntry.slicing_started_at)
                .where(PrintQueueEntry.id == queue_entry_id)
                .execution_options(populate_existing=True)
            ).first()
            session.rollback()
            return row is not None and row[0] == QueueState.SLICING and row[1] == claimed_at

        try:
            outcome = _slice(session, settings, client, entry, keep=_still_ours)
        except SlicerCancelled:
            # The user cancelled (the API already moved the entry on); only
            # touch it if it somehow still claims to be this very slice.
            current = _lock_claimed(session, queue_entry_id, claimed_at)
            if current is not None:
                current.state = QueueState.FAILED
                current.last_error = "cancelled"
                current.slicing_started_at = None
                session.commit()
            return
        except SlicerUnavailable as exc:
            # Doesn't consume an attempt -- see module docstring.
            entry = _lock_claimed(session, queue_entry_id, claimed_at)
            if entry is None:
                return
            entry.last_error = str(exc)
            entry.state = QueueState.WAITING
            entry.slicing_started_at = None
            session.commit()
            return
        except Exception as exc:  # noqa: BLE001 -- never leave the row `slicing`
            if not isinstance(exc, (SlicerError, ValueError)):
                logger.exception("slice_file %s crashed", queue_entry_id)
                session.rollback()
            entry = _lock_claimed(session, queue_entry_id, claimed_at)
            if entry is None:
                return
            entry.attempts += 1
            entry.last_error = _public_error(exc)
            entry.last_log = getattr(exc, "log_tail", None)
            entry.state = (
                QueueState.FAILED
                if entry.attempts >= MAX_ATTEMPTS or _is_permanent(exc)
                else QueueState.WAITING
            )
            entry.slicing_started_at = None
            terminal = entry.state == QueueState.FAILED
            if terminal:
                # All-plates batch: the siblings share this one slice, so they
                # fail with it instead of each re-slicing (one notification below).
                for sib in slice_siblings(session, entry):
                    if sib.sliced_file_id is None and sib.state in (
                        QueueState.WAITING,
                        QueueState.SLICING,
                    ):
                        sib.state = QueueState.FAILED
                        sib.last_error = entry.last_error
                        sib.slicing_started_at = None
            name = _entry_name(session, entry)
            entry_error = entry.last_error
            file_id = entry.file_id
            session.commit()
            _publish(
                settings,
                state="failed" if terminal else "waiting",
                queue_id=queue_entry_id,
                file_id=file_id,
            )
            if terminal:
                _notify(
                    "slice_failed",
                    {"name": name, "error": entry_error, "queue_id": queue_entry_id},
                )
            return

        # Re-fetch (bypassing the identity map) right before writing the
        # result back: a long HTTP slice gives `_reconcile` (stale-slicing
        # recovery) or a user (manual retry) plenty of time to have reset
        # this very row out from under us while it ran. If it's no longer
        # the same `slicing` attempt we claimed, the result is stale --
        # discard it rather than clobbering whatever state it's in now.
        current = _lock_claimed(session, queue_entry_id, claimed_at)
        if current is None:
            logger.warning(
                "slice result for queue entry %s discarded: entry changed during slicing",
                queue_entry_id,
            )
            _discard_new_file(session, settings, outcome)
            return

        new_file_id = outcome.file_id
        current.sliced_file_id = new_file_id
        current.state = QueueState.READY
        current.slicing_started_at = None
        current.last_error = None
        current.last_log = None
        if current.cost_center_id is not None:
            est = estimate_item_sync(session, settings, current)
            billing.reserve(
                session,
                cfg,
                current,
                Decimal(str(est)) if est is not None else None,
                user_id=current.created_by_user_id,
            )
        done_payload = _done_payload(session, current.file_id, new_file_id, current.recipe_id)
        done_payload["queue_id"] = current.id
        file_id = current.file_id
        session.commit()
        _publish(
            settings,
            state="done",
            queue_id=queue_entry_id,
            file_id=file_id,
            sliced_file_id=new_file_id,
        )
        _notify("slice_done", done_payload)


def _entry_name(session: Session, entry: PrintQueueEntry) -> str:
    file = session.get(File, entry.file_id) if entry.file_id else None
    return Path(file.rel_path).name if file is not None else f"queue #{entry.id}"


def _done_payload(
    session: Session, file_id: int, sliced_file_id: int, recipe_id: int | None
) -> dict:
    """Template context of the `slice_done` notification."""
    from app.models.processing import BlobMeta

    source = session.get(File, file_id)
    sliced = session.get(File, sliced_file_id)
    recipe = session.get(PrintRecipe, recipe_id) if recipe_id else None
    meta = session.get(BlobMeta, sliced.blob_hash) if sliced is not None else None
    return {
        "name": Path(source.rel_path).name if source is not None else "file",
        "recipe": recipe.name if recipe is not None else None,
        "print_time_s": meta.print_time_s if meta is not None else None,
        "filament_g": meta.filament_g if meta is not None else None,
    }


def _sliced_rel_path(source_rel_path: str, recipe_name: str, tag: str, plate: int) -> str:
    """Unique per slice run (`tag`: `q<entry id>` or `s<job id>`) + plate --
    never collides across two runs slicing the same source file with the same
    recipe (C1), so a re-slice never needs to delete a prior sliced ``File``
    someone else (e.g. a ``PrintJob``) may already reference."""
    stem = source_rel_path
    for suffix in (".gcode.3mf", ".3mf"):
        if source_rel_path.endswith(suffix):
            stem = source_rel_path[: -len(suffix)]
            break
    else:
        stem = Path(source_rel_path).stem
    return f"{stem} - {recipe_name} ({tag} p{plate}).gcode.3mf"


def _guard_arrange_spill(src_path: Path, out_path: Path, meta, plate: int | None) -> None:
    """Auto-arrange can move parts onto more plates than the source has, or
    off the one plate being sliced; only the plates the print flow knows about
    are ever printed, so those parts would silently vanish. Fail instead."""
    src_counts = plates3mf.source_object_counts(src_path)
    src_plates = len(src_counts) if src_counts else 1
    if not plate:
        if len(meta.plates) > src_plates:
            raise ValueError(
                f"auto-arrange spread the parts over {len(meta.plates)} plates (the file has "
                f"{src_plates}); the extra plates would not be printed. Turn off auto-arrange "
                "or use a larger bed."
            )
        return
    wanted = (src_counts or {}).get(plate)
    got = plates3mf.sliced_object_count(out_path)
    if wanted and got is not None and got < wanted:
        raise ValueError(
            f"auto-arrange could not fit all {wanted} parts of plate {plate} on one plate "
            f"({got} were sliced); the rest would be dropped. Turn off auto-arrange or use a "
            "larger bed."
        )


@dataclass
class SliceOutcome:
    file_id: int
    meta: dict
    created: bool = False  # True: this run stored a brand-new File
    log_tail: str | None = None  # slicer CLI output of this run (when the sidecar sent it)


def _meta_summary(meta: slicedmeta.SlicedMeta) -> dict:
    return {
        "print_time_s": meta.print_time_s,
        "filament_g": meta.filament_g,
        "filament_m": meta.filament_m,
        "filament_types": list(meta.filament_types),
        "layer_height": meta.layer_height,
        "nozzle": meta.nozzle,
        "printer_model": meta.printer_model,
        "plate_count": meta.plate_count,
        "plates": [
            {
                "index": p.get("index"),
                "prediction_s": p.get("prediction_s"),
                "weight_g": p.get("weight_g"),
            }
            for p in meta.plates
        ],
    }


def _slice(
    session: Session,
    settings,
    client: SlicerClient,
    entry: PrintQueueEntry,
    keep: Callable[[], bool] | None = None,
) -> SliceOutcome:
    """Queue flavour of `slice_source`."""
    if entry.file_id is None:
        raise ValueError("queue entry has no source file")
    all_plates = bool((entry.slice_options or {}).get(ALL_PLATES_KEY))
    if all_plates:
        # Print-all-plates: a sibling plate entry already sliced the whole
        # project -- reuse that file instead of slicing again.
        for sib in slice_siblings(session, entry):
            if sib.sliced_file_id is not None:
                return SliceOutcome(sib.sliced_file_id, {})
    return slice_source(
        session,
        settings,
        client,
        file_id=entry.file_id,
        recipe_id=entry.recipe_id,
        plate=None if all_plates else entry.plate,
        tag=f"q{entry.id}",
        job_id=queue_job_id(entry.id),
        keep=keep,
        option_overrides=entry.slice_options,
    )


def _is_permanent(exc: BaseException) -> bool:
    """Deterministic failures that a retry can never fix."""
    if isinstance(exc, ValueError):
        return True
    if isinstance(exc, SlicerError) and exc.status == 400:
        return True  # the sidecar rejected the request itself; resending can't help
    if not (isinstance(exc, SlicerError) and exc.status == 422):
        return False
    # The sidecar 422s every SliceFailed, incl. OOM/crash/no-output (retryable).
    text = f"{exc}\n{exc.log_tail or ''}"
    if parse_range_error(text) or _CONFIG_ERR in text:
        return True
    m = _EXIT_RE.search(text)
    return bool(m) and int(m.group(1)) not in _TRANSIENT_EXITS


_CONFIG_ERR = "Param values in 3mf/config error"
_EXIT_RE = re.compile(r"slicer exited (-?\d+)")
_TRANSIENT_EXITS = {134, 137, 139}


_MAX_RANGE_RETRIES = 8


def _slice_with_sanitize(
    client,
    *,
    printer_profile,
    process_profile,
    filament_profiles,
    sanitize_model: Path | None = None,
    **kw,
):
    """Sanitize known-bad values up front; on a 422 range error, clamp the
    offending key everywhere and retry within the same attempt.
    ``process_profile`` None = slice with the model file's own process
    settings; ``sanitize_model`` (that 3MF's temp copy) is then clamped too,
    since its embedded values reach the slicer unfiltered."""
    for d in (printer_profile, process_profile, *filament_profiles):
        if isinstance(d, dict) and "include" in d:
            # ValueError is a permanent failure (see `_is_permanent`).
            raise ValueError(
                f'Profile "{d.get("name", "?")}" is incomplete (unresolved include '
                "from an older slicer bundle) — re-sync profiles from the slicer sidecar."
            )
    printer, changed_p = sanitize_profile(printer_profile)
    process, changed_x = (
        sanitize_profile(process_profile) if process_profile is not None else (None, [])
    )
    filaments = []
    changed_f: list[str] = []
    for f in filament_profiles:
        fixed, ch = sanitize_profile(f)
        filaments.append(fixed)
        changed_f += ch
    changed_m = (
        plates3mf.clamp_project_settings(sanitize_model, KNOWN_RANGES)
        if sanitize_model is not None
        else []
    )
    if changed_p or changed_x or changed_f or changed_m:
        logger.info(
            "sanitized profile keys: %s", sorted({*changed_p, *changed_x, *changed_f, *changed_m})
        )
    for attempt in range(_MAX_RANGE_RETRIES + 1):
        try:
            return client.slice(
                printer_profile=printer, process_profile=process, filament_profiles=filaments, **kw
            )
        except SlicerError as exc:
            if exc.status != 422 or attempt == _MAX_RANGE_RETRIES:
                raise
            parsed = parse_range_error(f"{exc}\n{exc.log_tail or ''}")
            if parsed is None:
                raise
            key, lo, hi = parsed
            drafts = [d for d in (printer, process, *filaments) if d is not None]
            changed = [clamp_key(d, key, lo, hi) for d in drafts]
            if sanitize_model is not None and plates3mf.clamp_project_settings(
                sanitize_model, {key: (lo, hi)}
            ):
                changed.append(True)
            if not any(changed):
                raise
            logger.info("retrying slice after clamping %s to [%s, %s]", key, lo, hi)
    raise AssertionError("unreachable")  # pragma: no cover


def slice_source(
    session: Session,
    settings,
    client: SlicerClient,
    *,
    file_id: int,
    recipe_id: int | None,
    plate: int | None,
    tag: str,
    job_id: str | None = None,
    keep: Callable[[], bool] | None = None,
    option_overrides: dict | None = None,
) -> SliceOutcome:
    """The shared core of every slice (queue entry or standalone slice job):
    slices ``file_id`` with ``recipe_id`` through ``client`` and stores the
    result as a new ``File`` on the same revision. ``keep`` is polled right
    before the result is stored; False (cancelled/deleted meanwhile) raises
    ``SlicerCancelled`` so no orphan File is created. Raises ``SlicerError``
    (incl. ``SlicerUnavailable``/``SlicerCancelled``) or ``ValueError``.
    """
    file = session.get(File, file_id)
    if file is None:
        raise ValueError("queue entry has no source file")
    revision = session.get(Revision, file.revision_id)
    if revision is None:
        raise ValueError("source file's revision is missing")
    model = session.get(Model, revision.model_id)
    if model is None:
        raise ValueError("source file's model is missing")

    recipe = session.get(PrintRecipe, recipe_id) if recipe_id else None
    if recipe is None:
        raise ValueError("queue entry has no recipe")

    printer_profile = session.get(SlicerProfile, recipe.printer_profile_id)
    process_profile = session.get(SlicerProfile, recipe.process_profile_id)
    merged_options = {**(recipe.options or {}), **(option_overrides or {})}
    # Slice with the file's own process settings: send no process profile (the
    # slicer keeps the 3MF's embedded one); printer + filament still come
    # from the recipe.
    file_settings = bool(merged_options.get(FILE_SETTINGS_KEY))
    if printer_profile is None or (process_profile is None and not file_settings):
        raise ValueError("recipe's printer/process profile is missing")

    filament_profiles = []
    for filament_id in recipe.filament_profile_ids or []:
        if filament_id is None:
            continue
        filament_profile = session.get(SlicerProfile, filament_id)
        if filament_profile is not None:
            filament_profiles.append(filament_profile.data)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        suffix = Path(file.rel_path).suffix or ".3mf"
        try:
            src_path = derivatives.fetch_blob_to_temp(
                session, settings, file.blob_hash, tmp_path, suffix
            )
        except LookupError as exc:
            raise ValueError(str(exc)) from exc

        if file_settings:
            embedded = None
            if suffix.lower() == ".3mf":
                try:
                    with zipfile.ZipFile(src_path) as zf:
                        embedded = plates3mf.read_embedded_process(zf)
                except (zipfile.BadZipFile, OSError):
                    embedded = None
            reason = file_settings_service.unusable_reason(embedded, printer_profile)
            if reason is not None:
                raise ValueError(reason)  # permanent: the choice can never work
        options = SliceOptions(
            auto_orient=bool(merged_options.get("auto_orient", False)),
            arrange=bool(merged_options.get("arrange", False)),
            plate=plate,
        )
        out_bytes = _slice_with_sanitize(
            client,
            model=src_path,
            filename=file.rel_path,
            printer_profile=printer_profile.data,
            process_profile=None if file_settings else process_profile.data,
            filament_profiles=filament_profiles,
            bed_type=recipe.bed_type,
            options=options,
            job_id=job_id,
            sanitize_model=src_path if file_settings else None,
        )

        run_log = getattr(client, "last_log_tail", None)
        out_path = tmp_path / "sliced.gcode.3mf"
        out_path.write_bytes(out_bytes)
        meta = slicedmeta.parse_gcode_3mf(out_path)

        expected_plate = plate if plate else 1  # 0/None = all plates -> plate 1 prints
        plate_indexes = {p["index"] for p in meta.plates}
        if plate_indexes and expected_plate not in plate_indexes:
            raise ValueError(f"sliced output has no plate {expected_plate}")

        if options.arrange:
            _guard_arrange_spill(src_path, out_path, meta, plate)

        if (
            meta.printer_model
            and printer_profile.printer_model
            and meta.printer_model != printer_profile.printer_model
        ):
            raise ValueError(
                f"sliced printer_model {meta.printer_model!r} doesn't match recipe profile "
                f"{printer_profile.printer_model!r}"
            )

        if keep is not None and not keep():
            raise SlicerCancelled("slice cancelled before the result was stored")

        new_rel_path = _sliced_rel_path(
            file.rel_path,
            f"{recipe.name}, file settings" if file_settings else recipe.name,
            tag,
            expected_plate,
        )
        digest = blake3(out_bytes).hexdigest()

        # C1: never delete an existing sliced File -- a PrintJob (or
        # another queue entry) may already reference it. A re-slice that
        # happens to produce byte-identical output (same digest) on the
        # same revision reuses that row instead of duplicating it; anything
        # else is stored as a brand-new File at the entry-unique path above.
        existing = (
            session.execute(
                select(File).where(File.revision_id == revision.id, File.blob_hash == digest)
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return SliceOutcome(existing.id, _meta_summary(meta), log_tail=run_log)

        # A retried entry re-slices to the same name; that earlier File must
        # survive (a PrintJob references it) and (revision_id, rel_path) is
        # unique, so pick a free " (2)", " (3)", ... path.
        new_rel_path = _unique_rel_path(session, revision.id, new_rel_path)

        spool.ensure_spool_dir(settings)
        token = uuid.uuid4()
        spool_file_path = spool.spool_path(settings, token)
        spool_file_path.write_bytes(out_bytes)

        staged = StagedFile(
            token=token,
            spool_path=spool_file_path,
            blob_hash=digest,
            size=len(out_bytes),
            rel_path=new_rel_path,
            kind=BlobKind.SLICED,
            format_=BlobFormat.GCODE_3MF,
        )
        new_file = library.store_imported_file_sync(
            session, model=model, revision=revision, staged=staged
        )
        return SliceOutcome(new_file.id, _meta_summary(meta), created=True, log_tail=run_log)


def _unique_rel_path(session, revision_id: int, rel_path: str) -> str:
    """``rel_path`` if no File in the revision uses it, else the first free
    ``<stem> (n).gcode.3mf`` (n >= 2)."""
    taken = set(
        session.execute(select(File.rel_path).where(File.revision_id == revision_id)).scalars()
    )
    if rel_path not in taken:
        return rel_path
    suffix = ".gcode.3mf"
    stem = rel_path[: -len(suffix)] if rel_path.endswith(suffix) else rel_path
    ext = suffix if rel_path.endswith(suffix) else ""
    n = 2
    while f"{stem} ({n}){ext}" in taken:
        n += 1
    return f"{stem} ({n}){ext}"


def _public_error(exc: BaseException) -> str:
    """Short, human last_error: a library/DB exception (SQL text + bound
    parameters) never reaches the queue row; its message is logged instead."""
    if isinstance(exc, (SlicerError, ValueError, SlicerUnavailable)):
        text = " ".join(str(exc).split())
        return text[:200] if text else type(exc).__name__
    return f"slicing failed ({type(exc).__name__}; see server log)"


# --- standalone slice jobs (`POST /files/{id}/slice`) ---------------------

SLICE_JOB_BUSY_RETRIES = 40
SLICE_JOB_BUSY_COUNTDOWN_S = 15


def _finish_slice_job(
    settings,
    job_id: uuid.UUID,
    *,
    state: str,
    error: str | None = None,
    log_tail: str | None = None,
    outcome: SliceOutcome | None = None,
) -> None:
    """Writes a terminal state unless the job was cancelled meanwhile (a
    cancel wins: its result is discarded, and so is a freshly stored file),
    then publishes + notifies."""
    with base.sync_session() as session:
        job = session.execute(
            select(SliceJob)
            .where(SliceJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if job is None or job.state not in ("queued", "running"):
            session.rollback()
            _discard_new_file(session, settings, outcome)
            return
        job.state = state
        job.error = error
        job.log_tail = log_tail if log_tail is not None else (outcome.log_tail if outcome else None)
        file_id = job.file_id
        payload: dict
        if outcome is not None:
            job.sliced_file_id = outcome.file_id
            job.meta = outcome.meta
            if job.pipeline_id is not None:
                from app.services import pipelines

                pipelines.apply_post_steps(session, job, outcome)
            payload = _done_payload(session, file_id, outcome.file_id, job.recipe_id)
        else:
            source = session.get(File, file_id)
            payload = {
                "name": Path(source.rel_path).name if source is not None else "file",
                "error": (error or "")[:300],
            }
        payload["slice_job_id"] = str(job_id)
        sliced_file_id = job.sliced_file_id
        session.commit()
    _publish(
        settings,
        state=state,
        slice_job_id=str(job_id),
        file_id=file_id,
        sliced_file_id=sliced_file_id,
    )
    _notify("slice_done" if state == "done" else "slice_failed", payload)


@celery_app.task(
    name="app.tasks.slicing.slice_job",
    bind=True,
    soft_time_limit=SLICE_TASK_SOFT_LIMIT_S,
    time_limit=SLICE_TASK_HARD_LIMIT_S,
)
def slice_job(self, job_id: str) -> None:
    """Runs one `SliceJob`: the same `slice_source` core as the queue's
    `slice_file`, with no queue entry, billing or printer involved."""
    settings = get_settings()
    jid = uuid.UUID(job_id)
    with base.sync_session() as session:
        job = session.execute(
            select(SliceJob).where(SliceJob.id == jid).with_for_update()
        ).scalar_one_or_none()
        if job is None or job.state != "queued":
            return
        job.state = "running"
        job.celery_id = getattr(self.request, "id", None) or job.celery_id
        file_id, recipe_id, plate = job.file_id, job.recipe_id, job.plate
        option_overrides = job.options
        session.commit()

    def _active() -> bool:
        with base.sync_session() as s3:
            st = s3.scalar(select(SliceJob.state).where(SliceJob.id == jid))
        return st == "running"

    _publish(settings, state="running", slice_job_id=job_id, file_id=file_id)

    with base.sync_session() as session, contextlib.ExitStack() as _closer:
        server_cfg = get_server_config_sync(session, settings)
        client = client_from_config(server_cfg)
        if client is not None:
            _closer.callback(client.close)
        if client is None:
            err = (
                "slicer_disabled"
                if not server_cfg.slicer_enabled
                else "slicer sidecar not configured"
            )
            _finish_slice_job(settings, jid, state="failed", error=err)
            return
        try:
            outcome = slice_source(
                session,
                settings,
                client,
                file_id=file_id,
                recipe_id=recipe_id,
                plate=plate,
                tag=f"s{jid.hex[:8]}",
                job_id=slice_job_sidecar_id(jid),
                keep=_active,
                option_overrides=option_overrides,
            )
        except SlicerCancelled:
            return  # the API already marked it cancelled
        except SlicerBusy as exc:
            if self.request.retries < SLICE_JOB_BUSY_RETRIES:
                with base.sync_session() as s2:
                    s2.execute(
                        update(SliceJob)
                        .where(SliceJob.id == jid, SliceJob.state == "running")
                        .values(state="queued")
                    )
                    s2.commit()
                raise self.retry(
                    countdown=SLICE_JOB_BUSY_COUNTDOWN_S,
                    max_retries=SLICE_JOB_BUSY_RETRIES,
                    exc=exc,
                ) from None
            _finish_slice_job(settings, jid, state="failed", error=str(exc))
            return
        except (SlicerError, ValueError) as exc:
            _finish_slice_job(
                settings,
                jid,
                state="failed",
                error=str(exc),
                log_tail=getattr(exc, "log_tail", None),
            )
            return
        except Exception as exc:  # noqa: BLE001 -- never leave the row `running`
            logger.exception("slice_job %s crashed", job_id)
            session.rollback()
            _finish_slice_job(
                settings, jid, state="failed", error=f"internal error: {type(exc).__name__}"
            )
            return
    _finish_slice_job(settings, jid, state="done", outcome=outcome)


SLICE_SWEEP_INTERVAL_S = 300
SLICE_JOB_STALE_GRACE_S = 1800  # on top of the slice timeout + busy-retry window


@celery_app.task(name="app.tasks.slicing.sweep_stuck_slice_jobs")
def sweep_stuck_slice_jobs() -> int:
    """Fails `SliceJob` rows stuck `queued`/`running` past the slice timeout
    (+ busy-retry window + grace): a lost message, a killed worker or an
    acks_late redelivery that found the row already claimed. Queue entries
    stuck `slicing` are covered by the dispatcher's `_reconcile`."""
    settings = get_settings()
    with base.sync_session() as session:
        timeout = get_server_config_sync(session, settings).slice_timeout_s
        cutoff = datetime.now(UTC) - timedelta(
            seconds=timeout
            + SLICE_JOB_BUSY_RETRIES * SLICE_JOB_BUSY_COUNTDOWN_S
            + SLICE_JOB_STALE_GRACE_S
        )
        ids = session.scalars(
            select(SliceJob.id).where(
                SliceJob.state.in_(("queued", "running")), SliceJob.updated_at < cutoff
            )
        ).all()
    for jid in ids:
        _finish_slice_job(settings, jid, state="failed", error="slice timed out")
    return len(ids)


PROFILE_SANITIZE_BATCH = 200


@celery_app.task(name="app.tasks.slicing.backfill_profile_sanitize")
def backfill_profile_sanitize(after: int = 0) -> int:
    """Self-draining startup backfill: sanitize synced profiles in place,
    recomputing ``data_hash`` so ``status_of`` is unchanged. Idempotent."""
    from app.models.enums import ProfileSource
    from app.services.cloud_presets import content_hash

    done = 0
    with base.sync_session() as session:
        rows = (
            session.execute(
                select(SlicerProfile)
                .where(SlicerProfile.source == ProfileSource.SYNCED, SlicerProfile.id > after)
                .order_by(SlicerProfile.id)
                .limit(PROFILE_SANITIZE_BATCH)
            )
            .scalars()
            .all()
        )
        last = after
        for row in rows:
            last = row.id
            fixed, changed = sanitize_profile(row.data or {})
            if not changed:
                continue
            # only rehash if the stored hash matched, i.e. no local edits to lose track of
            was_clean = row.data_hash == content_hash(row.data or {})
            row.data = fixed
            if was_clean:
                row.data_hash = content_hash(fixed)
            done += 1
        session.commit()
        full = len(rows) >= PROFILE_SANITIZE_BATCH
    if full:
        backfill_profile_sanitize.apply_async(kwargs={"after": last}, countdown=2)
    return done
