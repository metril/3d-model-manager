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

import tempfile
import uuid
from pathlib import Path

from blake3 import blake3
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.importers.download import StagedFile
from app.models import PrintRecipe, SlicerProfile
from app.models.enums import BlobFormat, BlobKind, QueueState
from app.models.library import File, Model, PrintQueueEntry, Revision
from app.pipeline import slicedmeta
from app.services import derivatives, library, spool
from app.services.app_config import get_app_config_sync
from app.services.slicer_client import (
    SliceOptions,
    SlicerClient,
    SlicerError,
    SlicerUnavailable,
    client_from_config,
)
from app.tasks import base
from app.tasks.celery_app import celery_app

MAX_ATTEMPTS = 3


@celery_app.task(name="app.tasks.slicing.slice_file")
def slice_file(queue_entry_id: int) -> None:
    settings = get_settings()
    with base.sync_session() as session:
        entry = session.execute(
            select(PrintQueueEntry).where(PrintQueueEntry.id == queue_entry_id).with_for_update()
        ).scalar_one_or_none()
        if entry is None or entry.state != QueueState.SLICING:
            return

        cfg = get_app_config_sync(session, settings)
        client = client_from_config(cfg)
        if client is None:
            entry.last_error = "slicer sidecar not configured"
            entry.state = QueueState.WAITING
            session.commit()
            return

        try:
            new_file_id = _slice(session, settings, client, entry)
        except SlicerUnavailable as exc:
            # Doesn't consume an attempt -- see module docstring.
            entry.last_error = str(exc)
            entry.state = QueueState.WAITING
            session.commit()
            return
        except (SlicerError, ValueError) as exc:
            entry.attempts += 1
            entry.last_error = str(exc)
            entry.state = (
                QueueState.FAILED if entry.attempts >= MAX_ATTEMPTS else QueueState.WAITING
            )
            session.commit()
            return

        entry.sliced_file_id = new_file_id
        entry.state = QueueState.READY
        session.commit()


def _sliced_rel_path(source_rel_path: str, recipe_name: str) -> str:
    stem = source_rel_path
    for suffix in (".gcode.3mf", ".3mf"):
        if source_rel_path.endswith(suffix):
            stem = source_rel_path[: -len(suffix)]
            break
    else:
        stem = Path(source_rel_path).stem
    return f"{stem} - {recipe_name}.gcode.3mf"


def _slice(session: Session, settings, client: SlicerClient, entry: PrintQueueEntry) -> int:
    """Slices ``entry``'s source file through ``client`` and stores the
    result as a new ``File`` on the same revision, returning its id.
    """
    file = session.get(File, entry.file_id)
    if file is None:
        raise ValueError("queue entry has no source file")
    revision = session.get(Revision, file.revision_id)
    if revision is None:
        raise ValueError("source file's revision is missing")
    model = session.get(Model, revision.model_id)
    if model is None:
        raise ValueError("source file's model is missing")

    recipe = session.get(PrintRecipe, entry.recipe_id) if entry.recipe_id else None
    if recipe is None:
        raise ValueError("queue entry has no recipe")

    printer_profile = session.get(SlicerProfile, recipe.printer_profile_id)
    process_profile = session.get(SlicerProfile, recipe.process_profile_id)
    if printer_profile is None or process_profile is None:
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

        options = SliceOptions(
            auto_orient=bool((recipe.options or {}).get("auto_orient", False)),
            arrange=bool((recipe.options or {}).get("arrange", False)),
            plate=entry.plate,
        )
        out_bytes = client.slice(
            model=src_path,
            filename=file.rel_path,
            printer_profile=printer_profile.data,
            process_profile=process_profile.data,
            filament_profiles=filament_profiles,
            bed_type=recipe.bed_type,
            options=options,
        )

        out_path = tmp_path / "sliced.gcode.3mf"
        out_path.write_bytes(out_bytes)
        meta = slicedmeta.parse_gcode_3mf(out_path)

        expected_plate = entry.plate if entry.plate is not None else 1
        plate_indexes = {p["index"] for p in meta.plates}
        if plate_indexes and expected_plate not in plate_indexes:
            raise ValueError(f"sliced output has no plate {expected_plate}")

        if (
            meta.printer_model
            and printer_profile.printer_model
            and meta.printer_model != printer_profile.printer_model
        ):
            raise ValueError(
                f"sliced printer_model {meta.printer_model!r} doesn't match recipe profile "
                f"{printer_profile.printer_model!r}"
            )

        new_rel_path = _sliced_rel_path(file.rel_path, recipe.name)

        existing = session.execute(
            select(File).where(File.revision_id == revision.id, File.rel_path == new_rel_path)
        ).scalar_one_or_none()
        if existing is not None:
            # Same pending-store guard `slicer_intake`'s sync twin uses
            # before replacing a same-rel_path File row.
            if library.file_store_pending_sync(session, existing):
                raise ValueError(f"{new_rel_path!r} is still processing; retry once stored")
            session.delete(existing)
            session.flush()

        digest = blake3(out_bytes).hexdigest()
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
        return new_file.id
