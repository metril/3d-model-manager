"""The per-blob processing pipeline driver (SPEC "Processing pipeline";
Global Constraints "Pipeline shape" / "Pipeline jobs" / "Failure semantics").

``PIPELINE_STEPS`` is the format-keyed step order (Global Constraints table,
verbatim); ``next_step`` walks it. Each step is a Celery task registered with
``@pipeline_step("<name>")`` into ``STEP_TASKS``, keyed by the bare step name
(matching ``PIPELINE_STEPS`` entries, NOT Celery's dotted task name). Task 2
registers no real steps -- ``extract_metadata``/``convert_to_glb``/
``optimize_glb``/``render_thumb``/``extract_embedded_thumbs`` land in Tasks
3-6, each calling straight into ``run_step`` (this module's shared runner)
from a task body that just resolves its own ``StepFn`` closure.

``enqueue_step_sync``/``start_pipeline_sync`` are deliberately a NO-OP for a
step name that isn't (yet) in ``STEP_TASKS``: Task 2 ships the real
``PIPELINE_STEPS`` table before any of its steps are implemented, so e.g. an
STL upload resolves ``next_step`` to ``"extract_metadata"`` well before Task
3 registers that name -- without this guard, every real upload would crash
dispatching a step nothing has implemented yet (SPEC Task 2 Accept: uploading
any file works end-to-end with the store job followed by "(empty or stubbed)
pipeline dispatch"). Once a task lands, its name simply starts resolving and
the pipeline picks up from wherever it last stopped.

Dispatch failures from ``enqueue_step_sync`` (including, under the test
suite's eager Celery mode, the dispatched step's own body raising) are
best-effort and never propagate to the caller -- mirroring
``services.jobs._publish``'s reasoning: the caller here is always mid-
pipeline (``start_pipeline_sync`` right after a store job just committed
``done``, or ``run_step``'s own tail right after marking the CURRENT step
``done``), so a problem dispatching the NEXT step must never bubble up and
retroactively flip that already-settled, unrelated job back to ``failed``.
Contrast this with ``services.jobs.retry_job``'s pipeline-step branch, where
the caller IS the thing being retried -- there, a dispatch failure is exactly
what the caller (a human clicking retry) needs to see.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import trimesh
from celery import Task
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.config import Settings, get_settings
from app.models import Blob, BlobMeta, Derivative, File, Job
from app.models.enums import BlobFormat, DerivativeKind, DerivativeStatus
from app.pipeline import convert, gcode_meta, meshload, render, slicedmeta, thumbs
from app.services import derivatives, jobs
from app.services.storage_config import resolve_backend_sync
from app.storage.base import StorageBackend
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Global Constraints "Pipeline shape": per-blob step order, keyed by format.
PIPELINE_STEPS: dict[BlobFormat, tuple[str, ...]] = {
    BlobFormat.STL: ("extract_metadata", "convert_to_glb", "optimize_glb", "render_thumb"),
    BlobFormat.OBJ: ("extract_metadata", "convert_to_glb", "optimize_glb", "render_thumb"),
    BlobFormat.THREEMF: (
        "extract_metadata",
        "extract_embedded_thumbs",
        "convert_to_glb",
        "optimize_glb",
        "render_thumb",
    ),
    BlobFormat.GCODE_3MF: ("extract_metadata", "extract_embedded_thumbs"),
    BlobFormat.GCODE: ("extract_metadata",),
    BlobFormat.STEP: ("convert_to_glb", "extract_metadata", "optimize_glb", "render_thumb"),
    BlobFormat.IGES: ("convert_to_glb", "extract_metadata", "optimize_glb", "render_thumb"),
    BlobFormat.PNG: ("render_thumb",),
    BlobFormat.JPG: ("render_thumb",),
    BlobFormat.WEBP: ("render_thumb",),
    # R13c: doc kinds never enter the GLB/thumb pipeline.
    BlobFormat.PDF: ("render_thumb",),
    BlobFormat.MD: (),
    BlobFormat.TXT: (),
    BlobFormat.DOCX: (),
    BlobFormat.OTHER: (),
}

# Populated by @pipeline_step("<name>"); keyed by bare step name.
STEP_TASKS: dict[str, Task] = {}

# Global Constraints "Failure semantics": I/O retries 3x exponential backoff,
# transient errors only -- NOT StorageKeyNotFound/parse errors, which are
# deterministic and retrying them would just waste time before the same
# failure. Tests monkeypatch the delays to zeros.
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (OSError, ConnectionError)
TRANSIENT_RETRY_DELAYS: tuple[float, ...] = (1.0, 4.0, 16.0)

# Formats that produce a `glb` derivative (Global Constraints "Pipeline
# shape" table): shared by `extract_metadata`'s CAD-vs-native-mesh branch
# below and `maybe_enqueue_assembly_sync`'s readiness check.
_MESH_FORMATS = (BlobFormat.STL, BlobFormat.OBJ, BlobFormat.THREEMF)
_CAD_FORMATS = (BlobFormat.STEP, BlobFormat.IGES)


class UnsupportedBlobError(Exception):
    """Raised by a step's ``StepFn`` when the blob is a format/shape the
    tool can never handle (Global Constraints "Failure semantics":
    format-can't-ever -> derivative ``status="unsupported"``, job ``done``).
    Derivative bookkeeping stays inside the step function; ``run_step`` only
    maps this to the ``"unsupported"`` outcome and proceeds as if the step
    succeeded.
    """


StepOutcome = Literal["done", "skipped", "unsupported"]
StepFn = Callable[[SyncSession, Settings, StorageBackend, Blob], StepOutcome]


def pipeline_step(name: str) -> Callable[[Callable[[str, str], None]], Task]:
    """Decorator registering a Celery task body under
    ``app.tasks.pipeline.<name>`` (Celery's dotted task name) and, keyed by
    the bare ``name``, in ``STEP_TASKS`` (what ``enqueue_step_sync``/
    ``retry_job`` dispatch through). The wrapped function's signature is the
    task body itself: ``(job_id: str, blob_hash: str) -> None`` -- Global
    Constraints "Pipeline jobs" task-args convention -- and is expected to
    call ``run_step`` with its own ``StepFn``.
    """

    def decorator(fn: Callable[[str, str], None]) -> Task:
        task = celery_app.task(name=f"app.tasks.pipeline.{name}")(fn)
        STEP_TASKS[name] = task
        return task

    return decorator


def next_step(fmt: BlobFormat, after: str | None) -> str | None:
    """The step that should run next for ``fmt``, given the name of the last
    completed step (``after``). ``after=None`` means "give me the first
    step". Returns ``None`` when there's nothing left to run: past the end of
    ``fmt``'s steps, ``fmt`` has no steps at all, or ``after`` isn't one of
    ``fmt``'s step names.
    """
    steps = PIPELINE_STEPS.get(fmt, ())
    if after is None:
        return steps[0] if steps else None
    try:
        index = steps.index(after)
    except ValueError:
        return None
    return steps[index + 1] if index + 1 < len(steps) else None


def _dispatch_best_effort_sync(
    session: SyncSession, job: Job, dispatch: Callable[[], object]
) -> None:
    """Run ``dispatch()`` (an ``apply_async`` call), absorbing any exception
    it raises rather than propagating it. Shared by ``enqueue_step_sync`` and
    ``maybe_enqueue_assembly_sync`` below -- both dispatch from deep inside
    ``run_step``'s tail, right after some OTHER, unrelated job's row was just
    committed to a terminal state, so a dispatch failure for the NEXT thing
    must never bubble up and retroactively flip that back to failed (see the
    module docstring's "Dispatch failures" section).
    """
    try:
        dispatch()
    except Exception as exc:
        session.refresh(job)
        if job.state == jobs.STATE_QUEUED:
            # The task body never got to run at all (a real broker-dispatch
            # failure) -- nothing else will ever mark this row, so it must
            # not strand `queued` forever.
            jobs.mark_failed(session, str(job.id), f"dispatch failed: {exc}")
        else:
            # Eager-mode test run: the task's own body already drove its job
            # to a terminal state (failed, typically) through its own
            # exception handling before re-raising here. That's the correct,
            # final word on ITS row -- nothing to do.
            logger.warning(
                "job %s (type %s) raised during dispatch; its own terminal state stands",
                job.id,
                job.type,
                exc_info=True,
            )


def enqueue_step_sync(session: SyncSession, *, step: str, blob_hash: str, file_id: int) -> None:
    """Create a queued ``jobs`` row for ``step`` and dispatch its Celery
    task -- a no-op if ``step`` has no registered task yet (see module
    docstring). Best-effort dispatch (see ``_dispatch_best_effort_sync``).
    """
    task = STEP_TASKS.get(step)
    if task is None:
        return

    job = jobs.create_job_sync(
        session, id=uuid.uuid4(), type=step, subject_type="file", subject_id=file_id
    )
    _dispatch_best_effort_sync(
        session, job, lambda: task.apply_async(args=[str(job.id), blob_hash], task_id=str(job.id))
    )


def start_pipeline_sync(session: SyncSession, *, blob_hash: str, file_id: int) -> None:
    """Enqueue the first pipeline step for a freshly-stored blob, if its
    format has any steps at all.
    """
    blob = session.get(Blob, blob_hash)
    if blob is None:
        return
    step = next_step(blob.format, None)
    if step is not None:
        enqueue_step_sync(session, step=step, blob_hash=blob_hash, file_id=file_id)


# Global Constraints "Pipeline shape": the formats whose glb derivative
# `render_assembly_thumb`'s readiness check waits on -- everything else
# (gcode/gcode_3mf/png/jpg/other) never produces a `glb` at all, so it's
# simply excluded from the "every mesh/cad blob is ok" count rather than
# blocking the assembly render forever.
_ASSEMBLY_RELEVANT_FORMATS = _MESH_FORMATS + _CAD_FORMATS


def _revision_mesh_cad_blob_hashes_stmt(revision_id: int):
    """Distinct mesh/cad-format blob hashes for the files in ``revision_id``
    -- the set ``render_assembly_thumb``'s readiness check requires an ``ok``
    ``glb`` derivative for. Shared statement builder: the sync/async
    readiness checks below only differ in how they execute it.
    """
    return (
        select(File.blob_hash)
        .join(Blob, Blob.hash == File.blob_hash)
        .where(File.revision_id == revision_id, Blob.format.in_(_ASSEMBLY_RELEVANT_FORMATS))
        .distinct()
    )


def _ok_glb_count_stmt(blob_hashes: list[str]):
    return select(func.count(Derivative.id)).where(
        Derivative.blob_hash.in_(blob_hashes),
        Derivative.kind == DerivativeKind.GLB,
        Derivative.status == DerivativeStatus.OK,
    )


def _in_flight_assembly_job_stmt(revision_id: int):
    return select(Job.id).where(
        Job.type == "render_assembly_thumb",
        Job.subject_type == "revision",
        Job.subject_id == revision_id,
        Job.state.in_((jobs.STATE_QUEUED, jobs.STATE_RUNNING)),
    )


def _revision_assembly_ready(session: SyncSession, revision_id: int) -> bool:
    """Whether every mesh/cad-format blob among ``revision_id``'s files has
    an ``ok`` ``glb`` derivative -- vacuously true when the revision has no
    mesh/cad content at all (``render_assembly_thumb`` itself then renders
    zero geometry and settles on ``unsupported``, per the interface -- that's
    still a valid, intentional outcome to trigger, not a reason to withhold
    the job).
    """
    blob_hashes = list(session.execute(_revision_mesh_cad_blob_hashes_stmt(revision_id)).scalars())
    if not blob_hashes:
        return True
    ok_count = session.scalar(_ok_glb_count_stmt(blob_hashes))
    return ok_count == len(blob_hashes)


async def _revision_assembly_ready_async(db: AsyncSession, revision_id: int) -> bool:
    """Async twin of ``_revision_assembly_ready`` for the API-side trigger."""
    blob_hashes = list(
        (await db.execute(_revision_mesh_cad_blob_hashes_stmt(revision_id))).scalars()
    )
    if not blob_hashes:
        return True
    ok_count = await db.scalar(_ok_glb_count_stmt(blob_hashes))
    return ok_count == len(blob_hashes)


def _assembly_job_in_flight(session: SyncSession, revision_id: int) -> bool:
    return session.scalar(_in_flight_assembly_job_stmt(revision_id).limit(1)) is not None


async def _assembly_job_in_flight_async(db: AsyncSession, revision_id: int) -> bool:
    return await db.scalar(_in_flight_assembly_job_stmt(revision_id).limit(1)) is not None


def maybe_enqueue_assembly_sync(session: SyncSession, *, blob_hash: str) -> None:
    """Replaces Task 2's no-op ``pipeline_completed_hook`` (SPEC pipeline row
    6): for every DISTINCT revision containing a file of ``blob_hash``, fire
    off ``render_assembly_thumb`` if the revision is ready (see
    ``_revision_assembly_ready``) and doesn't already have one queued/running
    (``_assembly_job_in_flight``). Called from ``run_step``'s tail once a
    blob finishes its OWN last pipeline step -- a blob shared by more than
    one revision can trigger more than one assembly job here, one per
    revision, which is correct: each revision's assembly composition is
    independent.
    """
    revision_ids = session.execute(
        select(File.revision_id).where(File.blob_hash == blob_hash).distinct()
    ).scalars()
    for revision_id in revision_ids:
        maybe_enqueue_assembly_for_revision_sync(session, revision_id)


def maybe_enqueue_assembly_for_revision_sync(session: SyncSession, revision_id: int) -> None:
    """Queue ``render_assembly_thumb`` for ONE revision if it's ready and
    none is already in flight (best-effort dispatch)."""
    if _revision_assembly_ready(session, revision_id) and not _assembly_job_in_flight(
        session, revision_id
    ):
        job = jobs.create_job_sync(
            session,
            id=uuid.uuid4(),
            type="render_assembly_thumb",
            subject_type="revision",
            subject_id=revision_id,
        )
        _dispatch_best_effort_sync(
            session,
            job,
            lambda job=job, revision_id=revision_id: render_assembly_thumb.apply_async(
                args=[str(job.id), revision_id], task_id=str(job.id)
            ),
        )


async def maybe_enqueue_assembly_async(db: AsyncSession, *, revision_id: int) -> None:
    """Async twin of ``maybe_enqueue_assembly_sync``, for the API-side
    triggers (``services.library.create_revision``/``delete_file``) that
    already know which revision changed rather than which blob finished.
    Dispatch failures are absorbed the same way (best-effort) rather than
    propagated: the caller here is always right after its OWN, unrelated
    change (a new revision, a file deletion) already committed successfully
    -- a problem enqueueing the assembly render must never turn that into a
    500.
    """
    if not await _revision_assembly_ready_async(db, revision_id):
        return
    if await _assembly_job_in_flight_async(db, revision_id):
        return
    job = await jobs.create_job(
        db,
        id=uuid.uuid4(),
        type="render_assembly_thumb",
        subject_type="revision",
        subject_id=revision_id,
    )
    try:
        render_assembly_thumb.apply_async(args=[str(job.id), revision_id], task_id=str(job.id))
    except Exception as exc:
        await db.refresh(job)
        if job.state == jobs.STATE_QUEUED:
            job.state = jobs.STATE_FAILED
            job.error = f"dispatch failed: {exc}"
            await db.commit()
        else:
            logger.warning(
                "render_assembly_thumb dispatch for revision %s (job %s) raised; its own"
                " terminal state stands",
                revision_id,
                job.id,
                exc_info=True,
            )


def run_step(job_id: str, blob_hash: str, step: str, fn: StepFn) -> None:
    """Shared runner for every pipeline step task (used by Tasks 3-6's
    ``@pipeline_step``-decorated bodies): marks the job running, loads the
    blob, calls ``fn`` (retrying transient I/O errors per
    ``TRANSIENT_ERRORS``/``TRANSIENT_RETRY_DELAYS``), marks the job done, and
    either enqueues the next step for this format or -- on the last step --
    calls ``maybe_enqueue_assembly_sync``. Any non-transient exception from
    ``fn`` (after transient retries are exhausted too) marks the job
    ``failed`` and re-raises; ``UnsupportedBlobError`` instead maps to the
    ``"unsupported"`` outcome and proceeds normally.
    """
    with base.sync_session() as session:
        jobs.mark_running(session, job_id)

    settings = get_settings()
    with base.sync_session() as session:
        blob = session.get(Blob, blob_hash)
        if blob is None:
            jobs.mark_failed(session, job_id, f"blob {blob_hash} not found")
            return
        fmt = blob.format
        # Workstream C task C2: no step function actually reads through this
        # anymore -- every step that needs a blob's bytes fetches them via
        # `derivatives.fetch_blob_to_temp(session, settings, ...)`, which
        # resolves the SPECIFIC file/backend it picks internally (a blob can
        # have verified copies on more than one backend). Kept only to avoid
        # churning the `StepFn` signature/every call site for a param that
        # would otherwise still need threading through for free.
        backend = resolve_backend_sync(session, settings)

    try:
        outcome: StepOutcome
        attempt = 0
        while True:
            try:
                with base.sync_session() as session:
                    blob = session.get(Blob, blob_hash)
                    if blob is None:
                        jobs.mark_failed(session, job_id, f"blob {blob_hash} not found")
                        return
                    outcome = fn(session, settings, backend, blob)
                break
            except UnsupportedBlobError:
                outcome = "unsupported"
                break
            except TRANSIENT_ERRORS as exc:
                if attempt >= len(TRANSIENT_RETRY_DELAYS):
                    raise
                logger.warning(
                    "transient error running pipeline step %s for blob %s (attempt %d): %s",
                    step,
                    blob_hash,
                    attempt + 1,
                    exc,
                    exc_info=True,
                )
                time.sleep(TRANSIENT_RETRY_DELAYS[attempt])
                attempt += 1

        logger.debug("pipeline step %s for blob %s finished: %s", step, blob_hash, outcome)

        with base.sync_session() as session:
            jobs.mark_done(session, job_id)
            job = session.get(Job, uuid.UUID(job_id))
            nxt = next_step(fmt, step)
            if nxt is not None:
                enqueue_step_sync(session, step=nxt, blob_hash=blob_hash, file_id=job.subject_id)
            else:
                maybe_enqueue_assembly_sync(session, blob_hash=blob_hash)
    except Exception as exc:
        with base.sync_session() as session:
            jobs.mark_failed(session, job_id, str(exc))
        raise


# ---------------------------------------------------------------------------
# extract_metadata (Task 3; SPEC pipeline row 1; RESEARCH §1/§3): mesh stats
# for stl/obj/3mf, sliced-file/gcode-header fields for gcode_3mf/gcode, and
# mesh stats read back off the already-converted GLB derivative for
# step/iges.
# ---------------------------------------------------------------------------


def _mesh_blob_meta(blob_hash: str, mesh: trimesh.Trimesh, tool: str) -> BlobMeta:
    """Build the ``BlobMeta`` row for a loaded mesh -- shared by the native
    stl/obj/3mf branch and the CAD (GLB-derived) branch below, which compute
    the same stats off differently-sourced ``trimesh.Trimesh`` objects.
    """
    return BlobMeta(
        blob_hash=blob_hash,
        triangle_count=len(mesh.faces),
        dims_mm=[float(x) for x in mesh.extents],
        volume_cm3=(mesh.volume / 1000) if mesh.is_watertight else None,
        surface_area_cm2=mesh.area / 100,
        is_watertight=bool(mesh.is_watertight),
        raw={"tool": tool},
    )


def _gcode_3mf_extra_meta(path: Path) -> gcode_meta.GcodeMeta | None:
    """Parse layer-count/infill/slicer comment metadata (R10-B) out of the
    first resolvable plate's embedded ``.gcode`` member inside a
    ``.gcode.3mf`` -- reusing the same ``model_settings.config`` plate/gcode
    mapping ``parse_gcode_3mf`` already resolves, rather than a second zip
    pass over slice_info. A zip member's stream reports ``seekable()==True``
    but a backward seek re-decompresses it from byte 0, so
    ``gcode_meta._read_head_tail`` reads it forward exactly once instead,
    capturing both head and tail comment blocks in a single pass.
    """
    with zipfile.ZipFile(path) as zf:
        model_settings = slicedmeta.read_zip_member(zf, slicedmeta.MODEL_SETTINGS_PATH)
        plate_files = slicedmeta.parse_model_settings(model_settings)
        gcode_member = next(
            (files["gcode_file"] for files in plate_files.values() if files.get("gcode_file")),
            None,
        )
        if gcode_member is None:
            return None
        try:
            with zf.open(gcode_member) as member_stream:
                return gcode_meta.parse_gcode_meta(member_stream)
        except KeyError:
            return None


def _sliced_blob_meta(
    blob_hash: str, sliced: slicedmeta.SlicedMeta, extra: gcode_meta.GcodeMeta | None
) -> BlobMeta:
    filament_types = list(sliced.filament_types)
    if extra is not None and extra.filament_type and extra.filament_type not in filament_types:
        filament_types.append(extra.filament_type)
    return BlobMeta(
        blob_hash=blob_hash,
        print_time_s=sliced.print_time_s,
        filament_g=sliced.filament_g,
        filament_m=sliced.filament_m,
        filament_types=filament_types,
        layer_height=sliced.layer_height or (extra.layer_height_mm if extra else None),
        nozzle=sliced.nozzle,
        printer_model=sliced.printer_model,
        plate_count=sliced.plate_count,
        layer_count=extra.layer_count if extra else None,
        infill_pct=extra.infill_pct if extra else None,
        slicer=extra.slicer if extra else None,
        raw={"plates": sliced.plates, "tool": "zipfile"},
    )


def _gcode_blob_meta(
    blob_hash: str, header: slicedmeta.GcodeMeta, extra: gcode_meta.GcodeMeta
) -> BlobMeta:
    return BlobMeta(
        blob_hash=blob_hash,
        print_time_s=header.print_time_s or extra.duration_s,
        filament_g=header.filament_g or extra.filament_g,
        filament_m=header.filament_m
        or (extra.filament_mm / 1000 if extra.filament_mm is not None else None),
        filament_types=[extra.filament_type] if extra.filament_type else [],
        layer_height=extra.layer_height_mm,
        layer_count=header.layer_count or extra.layer_count,
        infill_pct=extra.infill_pct,
        slicer=extra.slicer,
        raw={"header": header.raw, "tool": "gcode-header"},
    )


def _cad_blob_meta(session: SyncSession, settings: Settings, blob: Blob) -> BlobMeta:
    """step/iges: read mesh stats off the already-converted GLB derivative
    (Global Constraints "Pipeline shape": ``convert_to_glb`` runs before
    ``extract_metadata`` for CAD formats specifically so OCCT only
    tessellates once) rather than re-tessellating the original CAD file.
    """
    glb = session.execute(
        select(Derivative).where(
            Derivative.blob_hash == blob.hash, Derivative.kind == DerivativeKind.GLB
        )
    ).scalar_one_or_none()
    if glb is None or glb.status != DerivativeStatus.OK:
        raise RuntimeError("glb derivative not ready; pipeline order broken")

    glb_path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)
    mesh = meshload.to_single_mesh(trimesh.load(glb_path))
    return _mesh_blob_meta(blob.hash, mesh, "glb-derived")


# Mirrors `_run_gltfpack`'s own `stderr_tail`/`[-2000:]` precedent -- a raw
# `str(exc)` from a deep parse-library failure (trimesh/lib3mf) can run long
# enough to be unpleasant to store/display; a few KB of context is plenty.
_DERIVATIVE_ERROR_MAX_LEN = 2000


def _fail_glb_derivative_from_metadata_step(
    session: SyncSession, blob_hash: str, exc: Exception
) -> None:
    """Record ``exc`` on the ``glb`` derivative when ``extract_metadata``'s
    OWN mesh load (below) fails for a mesh-format (stl/obj/3mf) blob.

    ``extract_metadata`` runs BEFORE ``convert_to_glb`` for every mesh format
    (``PIPELINE_STEPS``), and both steps parse the exact same source bytes
    through the exact same loader (``meshload.load_mesh``) -- so a failure
    here is deterministic and would identically doom ``convert_to_glb`` too.
    But a failed step never dispatches its successor (``run_step`` only
    enqueues ``next_step`` from its success path), so ``convert_to_glb`` --
    the step that actually owns the ``glb`` derivative row -- never gets to
    run at all, and never gets the chance to record its own ``failed`` row
    the way ``_convert_to_glb_step`` normally would. Without this, the blob
    is left with a failed ``Job`` but NO ``glb`` derivative row whatsoever
    (Global Constraints "Failure semantics" calls for a `failed` row, not an
    absent one) -- this mirrors ``_convert_to_glb_step``'s own mark-failed
    pattern so that gap can't reopen.
    """
    deriv = derivatives.upsert_derivative(session, blob_hash, DerivativeKind.GLB)
    derivatives.mark_derivative(
        session, deriv, status=DerivativeStatus.FAILED, error=str(exc)[:_DERIVATIVE_ERROR_MAX_LEN]
    )


def _extract_metadata_step(
    session: SyncSession, settings: Settings, backend: StorageBackend, blob: Blob
) -> StepOutcome:
    """``extract_metadata``'s ``StepFn``: skip if a ``BlobMeta`` row already
    exists for this blob (Global Constraints "Pipeline jobs": steps are
    idempotent), otherwise branch on ``blob.format`` and upsert one.
    """
    if session.get(BlobMeta, blob.hash) is not None:
        return "skipped"

    fmt = blob.format
    if fmt in _MESH_FORMATS:
        with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
            path = derivatives.fetch_blob_to_temp(
                session, settings, blob.hash, Path(tmp), f".{fmt.value}"
            )
            try:
                mesh, tool = meshload.load_mesh(path, fmt)
            except Exception as exc:
                _fail_glb_derivative_from_metadata_step(session, blob.hash, exc)
                raise
        meta = _mesh_blob_meta(blob.hash, mesh, tool)
    elif fmt is BlobFormat.GCODE_3MF:
        with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
            path = derivatives.fetch_blob_to_temp(session, settings, blob.hash, Path(tmp), ".3mf")
            meta = _sliced_blob_meta(
                blob.hash, slicedmeta.parse_gcode_3mf(path), _gcode_3mf_extra_meta(path)
            )
    elif fmt is BlobFormat.GCODE:
        with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
            path = derivatives.fetch_blob_to_temp(session, settings, blob.hash, Path(tmp), ".gcode")
            with path.open("rb") as fh:
                extra = gcode_meta.parse_gcode_meta(fh)
            meta = _gcode_blob_meta(blob.hash, slicedmeta.parse_gcode_header(path), extra)
    elif fmt in _CAD_FORMATS:
        meta = _cad_blob_meta(session, settings, blob)
    else:
        # png/jpg/other never route extract_metadata here at all (Global
        # Constraints "Pipeline shape" table has no extract_metadata entry
        # for them) -- defensive only.
        raise UnsupportedBlobError(f"extract_metadata: unsupported format {fmt}")

    session.merge(meta)
    session.commit()
    return "done"


@pipeline_step("extract_metadata")
def extract_metadata(job_id: str, blob_hash: str) -> None:
    run_step(job_id, blob_hash, "extract_metadata", _extract_metadata_step)


# ---------------------------------------------------------------------------
# extract_embedded_thumbs (Task 4; SPEC pipeline row 2; RESEARCH §3/§4:
# assimp can't parse Bambu 3MF, so this step -- reading the slicer's own
# embedded preview PNG(s) -- is the only thumbnail source for `3mf`/
# `gcode_3mf` until a mesh render exists; `gcode_3mf` never reaches
# convert_to_glb/render_thumb at all (Global Constraints "Pipeline shape").
# ---------------------------------------------------------------------------

# The one hardcoded guess this step is allowed to make when
# `model_settings.config` doesn't resolve a thumbnail at all (missing,
# unparseable, or every `thumbnail_file` entry points at a member that isn't
# actually in the zip) -- SPEC pipeline row 2: "fallback probe
# Metadata/plate_1.png -- never assume beyond that".
_FALLBACK_THUMBNAIL_MEMBER = "Metadata/plate_1.png"
_PLATE_ONE_INDEX = 1


def _resolve_plate_thumbnails(zf: zipfile.ZipFile) -> list[tuple[int, str]]:
    """Per-plate ``(index, zip member name)`` pairs for every plate whose
    ``model_settings.config`` ``thumbnail_file`` entry resolves to a member
    actually present in the zip, ascending by index (empty if
    `model_settings.config` is missing/unparseable/has no plates at all).
    Falls back to a single ``(1, _FALLBACK_THUMBNAIL_MEMBER)`` entry when
    that resolves nothing but the fixed fallback path exists anyway.
    """
    names = set(zf.namelist())
    model_settings = slicedmeta.read_zip_member(zf, slicedmeta.MODEL_SETTINGS_PATH)
    plate_files = slicedmeta.parse_model_settings(model_settings)

    resolved = sorted(
        (index, thumb)
        for index, info in plate_files.items()
        if (thumb := info.get("thumbnail_file")) and thumb in names
    )
    if resolved:
        return resolved
    if _FALLBACK_THUMBNAIL_MEMBER in names:
        return [(_PLATE_ONE_INDEX, _FALLBACK_THUMBNAIL_MEMBER)]
    return []


def _thumbs_already_ok(session: SyncSession, blob_hash: str) -> bool:
    """Global Constraints "Pipeline jobs": idempotent -- both thumb
    derivatives already ``ok`` means this step has nothing left to do.
    """
    ok_kinds = set(
        session.execute(
            select(Derivative.kind).where(
                Derivative.blob_hash == blob_hash,
                Derivative.kind.in_((DerivativeKind.THUMB_1024, DerivativeKind.THUMB_256)),
                Derivative.status == DerivativeStatus.OK,
            )
        ).scalars()
    )
    return ok_kinds == {DerivativeKind.THUMB_1024, DerivativeKind.THUMB_256}


def _publish_plate_thumb(data: bytes, settings: Settings, blob_hash: str, index: int) -> None:
    """Copy one plate's embedded PNG bytes verbatim to its rowless
    ``plate_thumb_path`` -- no decoding/validation (Bambu's known
    blank-when-headless PNGs are accepted as-is), just an atomic publish.
    """
    derivatives.publish_bytes(data, derivatives.plate_thumb_path(settings, blob_hash, index))


def _publish_image_thumbs(
    session: SyncSession, settings: Settings, blob_hash: str, src: Path | bytes, tool: str
) -> None:
    """Build+publish the thumb_1024/thumb_256 derivatives from any single
    source image that's already a plain, already-framed picture -- an
    embedded 3mf/gcode_3mf preview PNG (``tool="embedded"``), a raw png/jpg
    blob (``tool="pillow"``), or ``render_thumb``'s own 1024 f3d render being
    downscaled to 256 (``tool="f3d"``; Global Constraints: one f3d render
    only, this second call to ``make_thumbs_from_image`` just resizes the
    PNG it already produced). A corrupted/unreadable source is a
    deterministic parse failure (Global Constraints "Failure semantics":
    derivative row ``failed`` with ``error``/``tool``, AND the job itself
    fails) -- ``thumbs.make_thumbs_from_image`` already turns Pillow's own
    ``OSError``-subclass decode failures into a plain ``ValueError`` so
    ``run_step`` never mistakes this for transient I/O.
    """
    deriv_1024 = derivatives.upsert_derivative(session, blob_hash, DerivativeKind.THUMB_1024)
    deriv_256 = derivatives.upsert_derivative(session, blob_hash, DerivativeKind.THUMB_256)
    try:
        p1024, p256 = thumbs.make_thumbs_from_image(src, settings, blob_hash)
    except Exception as exc:
        derivatives.mark_derivative(
            session, deriv_1024, status=DerivativeStatus.FAILED, tool=tool, error=str(exc)
        )
        derivatives.mark_derivative(
            session, deriv_256, status=DerivativeStatus.FAILED, tool=tool, error=str(exc)
        )
        raise
    derivatives.mark_derivative(
        session, deriv_1024, status=DerivativeStatus.OK, local_path=str(p1024), tool=tool
    )
    derivatives.mark_derivative(
        session, deriv_256, status=DerivativeStatus.OK, local_path=str(p256), tool=tool
    )


def _extract_embedded_thumbs_step(
    session: SyncSession, settings: Settings, backend: StorageBackend, blob: Blob
) -> StepOutcome:
    """``extract_embedded_thumbs``'s ``StepFn``: resolve embedded preview
    PNG(s) out of the 3mf/gcode_3mf zip via ``model_settings.config`` (or the
    fixed fallback probe), then branch on format -- ``gcode_3mf`` extracts
    every resolved plate to its own rowless ``plate_thumb_path`` and, if
    plate 1 was among them, also builds the thumb derivatives from it;
    plain ``3mf`` project files only ever build the thumb derivatives, from
    the first (lowest-index) resolved plate. No resolvable thumbnail at all
    is not a failure -- ``render_thumb`` (Task 6) will rasterize a mesh
    later, so a sliced blob simply has no thumb until then (SPEC pipeline
    row 2).
    """
    if _thumbs_already_ok(session, blob.hash):
        return "skipped"

    with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
        path = derivatives.fetch_blob_to_temp(session, settings, blob.hash, Path(tmp), ".3mf")
        with zipfile.ZipFile(path) as zf:
            plates = _resolve_plate_thumbnails(zf)
            plate_bytes = {index: zf.read(member) for index, member in plates}

    if blob.format is BlobFormat.GCODE_3MF:
        for index, data in plate_bytes.items():
            _publish_plate_thumb(data, settings, blob.hash, index)
        source_bytes = plate_bytes.get(_PLATE_ONE_INDEX)
    else:
        source_bytes = next(iter(plate_bytes.values()), None)

    if source_bytes is None:
        return "done"

    _publish_image_thumbs(session, settings, blob.hash, source_bytes, "embedded")
    return "done"


@pipeline_step("extract_embedded_thumbs")
def extract_embedded_thumbs(job_id: str, blob_hash: str) -> None:
    run_step(job_id, blob_hash, "extract_embedded_thumbs", _extract_embedded_thumbs_step)


# ---------------------------------------------------------------------------
# convert_to_glb (Task 5; SPEC pipeline row 3; RESEARCH §2): per-format
# conversion to the raw, uncompressed `glb` derivative -- the one GLB
# artifact f3d/trimesh/OCCT ever read back (Global Constraints "Two GLB
# artifacts per blob"; ``optimize_glb`` below produces the meshopt-compressed
# browser artifacts separately, never touching this row). The actual
# trimesh/lib3mf/cascadio/OCP conversion work lives in
# ``app.pipeline.convert``/``app.pipeline.cad``; this step is just the
# derivative/job bookkeeping around it, matching ``extract_metadata``'s shape.
# ---------------------------------------------------------------------------


def _glb_derivative(session: SyncSession, blob_hash: str) -> Derivative | None:
    return session.execute(
        select(Derivative).where(
            Derivative.blob_hash == blob_hash, Derivative.kind == DerivativeKind.GLB
        )
    ).scalar_one_or_none()


def _convert_to_glb_step(
    session: SyncSession, settings: Settings, backend: StorageBackend, blob: Blob
) -> StepOutcome:
    """``convert_to_glb``'s ``StepFn``: skip if the ``glb`` derivative is
    already ``ok`` (Global Constraints "Pipeline jobs": idempotent), otherwise
    convert into a temp file staged in the derivative's own parent directory
    (so the final ``publish_file`` rename never crosses a filesystem
    boundary -- ``convert.convert_to_glb_file``'s trimesh/cascadio/OCP calls
    write to that path directly, unlike the in-memory-bytes steps that use
    ``derivatives.publish_bytes``) and publishes it.
    """
    existing = _glb_derivative(session, blob.hash)
    if existing is not None and existing.status == DerivativeStatus.OK:
        return "skipped"

    deriv = derivatives.upsert_derivative(session, blob.hash, DerivativeKind.GLB)
    dest = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".tdmm-glb-", suffix=".glb")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
            src = derivatives.fetch_blob_to_temp(
                session, settings, blob.hash, Path(tmp), f".{blob.format.value}"
            )
            tool = convert.convert_to_glb_file(src, blob.format, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        derivatives.mark_derivative(session, deriv, status=DerivativeStatus.FAILED, error=str(exc))
        raise
    derivatives.publish_file(tmp_path, dest)
    derivatives.mark_derivative(
        session, deriv, status=DerivativeStatus.OK, local_path=str(dest), tool=tool
    )
    return "done"


@pipeline_step("convert_to_glb")
def convert_to_glb(job_id: str, blob_hash: str) -> None:
    run_step(job_id, blob_hash, "convert_to_glb", _convert_to_glb_step)


# ---------------------------------------------------------------------------
# optimize_glb (Task 5; SPEC pipeline row 4; RESEARCH §5): gltfpack `-cc`
# meshopt-compresses the ok `glb` derivative into the rowless, browser-only
# `glb_web` file (Global Constraints "Two GLB artifacts per blob" -- the raw
# `glb` derivative this reads is NEVER rewritten), plus a `-si 0.5` decimated
# LOD (`glb_preview` DERIVATIVE row, unlike `glb_web`) once the blob's own
# triangle count clears ``PREVIEW_TRIANGLE_THRESHOLD``.
# ---------------------------------------------------------------------------

# SPEC pipeline row 4 / RESEARCH §5: LOD threshold above which a decimated
# `-si 0.5` preview is also generated. Single reference for this module and
# the Task 8 frontend mirror.
PREVIEW_TRIANGLE_THRESHOLD = 1_500_000


def _run_gltfpack(settings: Settings, args: list[str]) -> None:
    """Invoke gltfpack, turning its two failure modes into clear,
    deterministic exceptions. A missing binary raises ``FileNotFoundError``
    (an ``OSError`` subclass) -- deliberately NOT let through as-is, since
    ``run_step``'s ``TRANSIENT_ERRORS`` retry loop treats any bare
    ``OSError`` as transient, which would waste the whole retry budget
    re-running a binary that will never exist before finally surfacing the
    wrong (generic) error anyway.
    """
    try:
        result = subprocess.run(
            [settings.gltfpack_path, *args], capture_output=True, timeout=300, text=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gltfpack not found -- run scripts/fetch-gltfpack.sh (dev) / check image (docker)"
        ) from exc
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip()[-2000:]
        raise RuntimeError(f"gltfpack failed (exit {result.returncode}): {stderr_tail}")


def _generate_preview_lod(
    session: SyncSession, settings: Settings, blob_hash: str, raw_glb: Path
) -> None:
    deriv = derivatives.upsert_derivative(session, blob_hash, DerivativeKind.GLB_PREVIEW)
    dest = derivatives.derivative_path(settings, blob_hash, DerivativeKind.GLB_PREVIEW)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".tdmm-glbpreview-", suffix=".glb")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        _run_gltfpack(settings, ["-i", str(raw_glb), "-o", str(tmp_path), "-si", "0.5", "-cc"])
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        derivatives.mark_derivative(
            session, deriv, status=DerivativeStatus.FAILED, tool="gltfpack -si 0.5", error=str(exc)
        )
        raise
    derivatives.publish_file(tmp_path, dest)
    derivatives.mark_derivative(
        session, deriv, status=DerivativeStatus.OK, local_path=str(dest), tool="gltfpack -si 0.5"
    )


def _glb_preview_derivative(session: SyncSession, blob_hash: str) -> Derivative | None:
    return session.execute(
        select(Derivative).where(
            Derivative.blob_hash == blob_hash, Derivative.kind == DerivativeKind.GLB_PREVIEW
        )
    ).scalar_one_or_none()


def _preview_lod_needed(session: SyncSession, blob_hash: str) -> bool:
    meta = session.get(BlobMeta, blob_hash)
    return (
        meta is not None
        and bool(meta.triangle_count)
        and meta.triangle_count > PREVIEW_TRIANGLE_THRESHOLD
    )


def _optimize_glb_step(
    session: SyncSession, settings: Settings, backend: StorageBackend, blob: Blob
) -> StepOutcome:
    """``optimize_glb``'s ``StepFn``: the ok `glb` derivative is a hard
    prerequisite (a broken pipeline order is a bug, not a retriable
    condition, hence the plain ``RuntimeError`` rather than a derivative
    failure -- there's no derivative row to fail for the rowless `glb_web`
    output anyway).

    Idempotent per-ARTIFACT, not behind a single skip gate (Important #2
    fix): `glb_web` and the `glb_preview` LOD are each checked and
    (re)produced independently. The step used to skip outright once
    `glb_web` existed, which made a failed preview LOD -- gltfpack `-cc`
    can succeed and publish `glb_web` in the same run where the later `-si
    0.5` pass then fails -- permanently unrecoverable via retry: the skip
    check fired before ever looking at the preview branch. Now a retry
    regenerates only what's actually missing/not-ok; an already-published
    `glb_web` is never redundantly recompressed.
    """
    glb_deriv = _glb_derivative(session, blob.hash)
    if glb_deriv is None or glb_deriv.status != DerivativeStatus.OK:
        raise RuntimeError("glb missing")

    web_path = derivatives.glb_web_path(settings, blob.hash)
    raw_glb = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)

    preview_needed = _preview_lod_needed(session, blob.hash)
    preview_deriv = _glb_preview_derivative(session, blob.hash)
    preview_satisfied = not preview_needed or (
        preview_deriv is not None and preview_deriv.status == DerivativeStatus.OK
    )

    if web_path.exists() and preview_satisfied:
        return "skipped"

    if not web_path.exists():
        web_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=web_path.parent, prefix=".tdmm-glbweb-", suffix=".glb")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            _run_gltfpack(settings, ["-i", str(raw_glb), "-o", str(tmp_path), "-cc"])
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        derivatives.publish_file(tmp_path, web_path)

    if preview_needed and not preview_satisfied:
        _generate_preview_lod(session, settings, blob.hash, raw_glb)

    return "done"


@pipeline_step("optimize_glb")
def optimize_glb(job_id: str, blob_hash: str) -> None:
    run_step(job_id, blob_hash, "optimize_glb", _optimize_glb_step)


# ---------------------------------------------------------------------------
# render_thumb (Task 6; SPEC pipeline rows 5-6; RESEARCH §4): mesh/cad blobs
# rasterize their already-converted, raw `glb` derivative via f3d at 1024
# then reuse `_publish_image_thumbs` (one f3d render only) to downscale to
# 256; png/jpg blobs go straight to `_publish_image_thumbs` on the original
# bytes.
# ---------------------------------------------------------------------------

_IMAGE_FORMATS = (BlobFormat.PNG, BlobFormat.JPG, BlobFormat.WEBP)


def _render_mesh_thumb(session: SyncSession, settings: Settings, blob: Blob) -> None:
    """Render the ok `glb` derivative via f3d, then hand the resulting PNG
    to `_publish_image_thumbs` for the 1024/256 derivative bookkeeping.

    Both thumb derivative rows are upserted BEFORE attempting the render
    (mirroring `_extract_embedded_thumbs_step`'s pattern) so a render
    failure -- not just a `make_thumbs_from_image` failure -- still leaves
    Global Constraints "Failure semantics" satisfied: `failed` rows with
    `error`/`tool`, not just a failed job with no row at all.
    """
    deriv_1024 = derivatives.upsert_derivative(session, blob.hash, DerivativeKind.THUMB_1024)
    deriv_256 = derivatives.upsert_derivative(session, blob.hash, DerivativeKind.THUMB_256)
    glb_path = derivatives.derivative_path(settings, blob.hash, DerivativeKind.GLB)
    with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
        rendered_png = Path(tmp) / "render.png"
        try:
            render.render_glb_png(glb_path, rendered_png, size=1024)
        except Exception as exc:
            derivatives.mark_derivative(
                session, deriv_1024, status=DerivativeStatus.FAILED, tool="f3d", error=str(exc)
            )
            derivatives.mark_derivative(
                session, deriv_256, status=DerivativeStatus.FAILED, tool="f3d", error=str(exc)
            )
            raise
        _publish_image_thumbs(session, settings, blob.hash, rendered_png, "f3d")


def _render_thumb_step(
    session: SyncSession, settings: Settings, backend: StorageBackend, blob: Blob
) -> StepOutcome:
    """``render_thumb``'s ``StepFn``: skip outright once both thumb
    derivatives are already ``ok`` -- covers the `3mf` case where
    `extract_embedded_thumbs` already served a thumbnail from the slicer's
    own embedded preview and there's nothing left for this step to do.
    """
    if _thumbs_already_ok(session, blob.hash):
        return "skipped"

    if blob.format in _IMAGE_FORMATS:
        with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
            path = derivatives.fetch_blob_to_temp(
                session, settings, blob.hash, Path(tmp), f".{blob.format.value}"
            )
            _publish_image_thumbs(session, settings, blob.hash, path, "pillow")
        return "done"

    if blob.format == BlobFormat.PDF:
        # First page via pypdfium2 (already a dependency for label PNGs).
        with tempfile.TemporaryDirectory(prefix="tdmm-pipe-") as tmp:
            path = derivatives.fetch_blob_to_temp(session, settings, blob.hash, Path(tmp), ".pdf")
            page_png = Path(tmp) / "page1.png"
            try:
                thumbs.render_pdf_first_page(path, page_png)
            except ValueError as exc:
                for kind in (DerivativeKind.THUMB_1024, DerivativeKind.THUMB_256):
                    d = derivatives.upsert_derivative(session, blob.hash, kind)
                    derivatives.mark_derivative(
                        session, d, status=DerivativeStatus.FAILED, tool="pdfium", error=str(exc)
                    )
                raise
            _publish_image_thumbs(session, settings, blob.hash, page_png, "pdfium")
        return "done"

    glb_deriv = _glb_derivative(session, blob.hash)
    if glb_deriv is None or glb_deriv.status != DerivativeStatus.OK:
        raise RuntimeError("glb missing")
    _render_mesh_thumb(session, settings, blob)
    return "done"


@pipeline_step("render_thumb")
def render_thumb(job_id: str, blob_hash: str) -> None:
    run_step(job_id, blob_hash, "render_thumb", _render_thumb_step)


# ---------------------------------------------------------------------------
# render_assembly_thumb (Task 6; SPEC pipeline row 6): NOT a `@pipeline_step`
# -- its subject is a REVISION, not a file/blob, so it manages its own job
# transitions the same way `app.tasks.ingest.store_to_backend` does, rather
# than going through `run_step`. Triggered by `maybe_enqueue_assembly_sync`/
# `maybe_enqueue_assembly_async` above whenever a revision's mesh/cad content
# becomes fully converted; always re-renders (the revision's file
# composition may have changed since the last run) rather than skipping on
# an existing `ok` `assembly_thumbs` row.
# ---------------------------------------------------------------------------


def _revision_glb_paths(session: SyncSession, settings: Settings, revision_id: int) -> list[Path]:
    """Ok `glb` derivative paths for every file in `revision_id`, in
    `rel_path` order -- gcode/image files (no `glb` at all) and any mesh/cad
    file whose conversion hasn't finished or failed are silently excluded,
    not a reason to fail the whole assembly render (a partial assembly is
    still more useful than none; `maybe_enqueue_assembly_sync`'s readiness
    check is what keeps this from firing on an obviously-incomplete
    revision in the first place).
    """
    blob_hashes = session.execute(
        select(File.blob_hash)
        .join(Derivative, Derivative.blob_hash == File.blob_hash)
        .where(
            File.revision_id == revision_id,
            Derivative.kind == DerivativeKind.GLB,
            Derivative.status == DerivativeStatus.OK,
        )
        .order_by(File.rel_path)
    ).scalars()
    return [
        derivatives.derivative_path(settings, blob_hash, DerivativeKind.GLB)
        for blob_hash in blob_hashes
    ]


@celery_app.task(name="app.tasks.pipeline.render_assembly_thumb")
def render_assembly_thumb(job_id: str, revision_id: int) -> None:
    settings = get_settings()
    try:
        with base.sync_session() as session:
            jobs.mark_running(session, job_id)
            derivatives.upsert_assembly_thumb(session, revision_id)
            glb_paths = _revision_glb_paths(session, settings, revision_id)

        if not glb_paths:
            with base.sync_session() as session:
                derivatives.mark_assembly_thumb(
                    session, revision_id, status=DerivativeStatus.UNSUPPORTED
                )
                jobs.mark_done(session, job_id)
            return

        scene = trimesh.Scene()
        for glb_path in glb_paths:
            scene.add_geometry(trimesh.load(glb_path))

        dest = derivatives.assembly_thumb_path(settings, revision_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".tdmm-assembly-", suffix=".png")
        os.close(fd)
        tmp_png = Path(tmp_name)
        try:
            with tempfile.TemporaryDirectory(prefix="tdmm-assembly-") as tmp:
                merged_glb = Path(tmp) / "merged.glb"
                scene.export(merged_glb, file_type="glb")
                render.render_glb_png(merged_glb, tmp_png, size=1024)
        except BaseException:
            tmp_png.unlink(missing_ok=True)
            raise
        derivatives.publish_file(tmp_png, dest)

        with base.sync_session() as session:
            derivatives.mark_assembly_thumb(
                session, revision_id, status=DerivativeStatus.OK, local_path=str(dest)
            )
            jobs.mark_done(session, job_id)
    except Exception as exc:
        with base.sync_session() as session:
            derivatives.mark_assembly_thumb(
                session, revision_id, status=DerivativeStatus.FAILED, error=str(exc)
            )
            jobs.mark_failed(session, job_id, str(exc))
        raise
