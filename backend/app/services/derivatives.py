"""The derivative store: pure path/DB helpers for the processing pipeline
(SPEC "Processing pipeline"; Global Constraints "Derivative store").

Every generated per-blob artifact -- thumbnails, the converted/optimized GLB,
per-plate preview PNGs, the meshopt browser GLB -- lives under
``{settings.data_dir}/derivatives/``, sharded into two levels of
subdirectories by the blob's own hash, NEVER inside the library tree
(originals are immutable -- the pipeline only ever reads library bytes via
``StorageBackend.read``). Two of the paths here (``plate_thumb_path``,
``glb_web_path``) are deliberately rowless: derived, deterministic filenames
with no ``derivatives`` table row, needed because the browser-only
meshopt-compressed GLB is a distinct artifact from the ``glb`` derivative row
(Global Constraints "Two GLB artifacts per blob" -- the row is always the
uncompressed conversion output; gltfpack's meshopt output can't be read back
by f3d/trimesh/OCCT).

This module is pure path/DB plumbing -- no Celery, no tool invocations. The
actual conversion/render steps (Tasks 2-6) call through it from the worker's
sync world (``upsert_derivative``/``mark_derivative``/``fetch_blob_to_temp``,
all plain ``sqlalchemy.orm.Session``); the path functions have no DB
dependency at all and are usable from either world.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AssemblyThumb, Derivative, File
from app.models.enums import DerivativeKind, DerivativeStatus
from app.services.storage_backends import resolve_backend_for_file_sync

logger = logging.getLogger(__name__)

SUFFIXES: dict[DerivativeKind, str] = {
    DerivativeKind.THUMB_256: "thumb_256.png",
    DerivativeKind.THUMB_1024: "thumb_1024.png",
    DerivativeKind.GLB: "glb",
    DerivativeKind.GLB_PREVIEW: "glb_preview.glb",
}


def _derivative_dir(settings: Settings, blob_hash: str) -> Path:
    """The two-level hash-sharded directory a blob's derivatives live under."""
    return Path(settings.data_dir) / "derivatives" / blob_hash[:2] / blob_hash[2:4]


def derivative_path(settings: Settings, blob_hash: str, kind: DerivativeKind) -> Path:
    """Path for a DB-tracked derivative (one ``derivatives`` row per
    ``(blob_hash, kind)`` -- see ``SUFFIXES``).
    """
    return _derivative_dir(settings, blob_hash) / f"{blob_hash}.{SUFFIXES[kind]}"


def plate_thumb_path(settings: Settings, blob_hash: str, index: int) -> Path:
    """Path for a sliced-3MF embedded plate thumbnail (rowless, like
    ``glb_web_path`` -- there's no bounded set of plates to model as a
    ``DerivativeKind``).
    """
    return _derivative_dir(settings, blob_hash) / f"{blob_hash}.plate_{index}.png"


def plate_glb_path(settings: Settings, blob_hash: str, index: int) -> Path:
    """Path for an unsliced-3MF per-plate GLB (rowless, like
    ``plate_thumb_path``): that plate's objects recentred onto one bed."""
    return _derivative_dir(settings, blob_hash) / f"{blob_hash}.plate_{index}.glb"


def objects_json_path(settings: Settings, blob_hash: str) -> Path:
    """Cached per-object listing of a 3MF (see ``services.object_assets``)."""
    return _derivative_dir(settings, blob_hash) / f"{blob_hash}.objects.json"


def object_glb_path(settings: Settings, blob_hash: str, index: int) -> Path:
    """Per-object GLB of a 3MF build item, on a single bed (rowless)."""
    return _derivative_dir(settings, blob_hash) / f"{blob_hash}.object_{index}.glb"


def glb_web_path(settings: Settings, blob_hash: str) -> Path:
    """Path for the meshopt-compressed, browser-only GLB (rowless: this is
    NOT the ``derivatives`` ``glb`` row, which is always the uncompressed
    conversion output -- see Global Constraints "Two GLB artifacts per
    blob"). ``GET /api/blobs/{hash}/glb`` serves this file when present, else
    falls back to the raw ``glb`` derivative.
    """
    return _derivative_dir(settings, blob_hash) / f"{blob_hash}.glb_web.glb"


def assembly_thumb_path(settings: Settings, revision_id: int) -> Path:
    """Path for a whole-revision assembly thumbnail (SPEC ``assembly_thumbs``,
    rendered per-revision rather than per-blob).
    """
    return Path(settings.data_dir) / "derivatives" / "assembly" / f"{revision_id}.png"


def publish_file(tmp: Path, dest: Path) -> None:
    """Atomically publish a worker-written temp file to its final derivative
    path: create ``dest``'s parent, then ``os.replace`` (same-filesystem
    rename, atomic) and relax the mode to a normal 0644.

    Mirrors ``LocalStorageBackend.write``'s M1 lesson: staging tools
    (``tempfile.mkstemp`` and friends) create temp files mode 0600 regardless
    of umask, which would otherwise leave derivative files readable only by
    the worker's own user.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    os.chmod(dest, 0o644)


def publish_bytes(data: bytes, dest: Path) -> None:
    """Stage ``data`` as a temp file in ``dest``'s own parent directory --
    guaranteeing the same filesystem as the final path, so ``publish_file``'s
    ``os.replace`` is never asked to cross a mount boundary -- then publish
    it. Shared by every step that already holds the final bytes in memory
    (embedded/rendered thumbnails, plate PNGs); a step whose own external
    tool writes its output straight to a path (``convert_to_glb``'s
    trimesh/cascadio/OCP, ``optimize_glb``'s gltfpack subprocess) stages its
    own ``tempfile.mkstemp`` directly instead, since there's no in-memory
    bytes blob to hand off here.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".tdmm-pub-", suffix=dest.suffix)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    publish_file(tmp_path, dest)


def upsert_derivative(session: Session, blob_hash: str, kind: DerivativeKind) -> Derivative:
    """Get-or-create the ``derivatives`` row for ``(blob_hash, kind)``.

    Re-running a step (manual retry, or a later revision sharing this blob)
    resets an existing row back to ``pending``/``error=None`` before the step
    redoes the work, so a previous run's terminal state can't linger --
    UNLESS the row is already ``ok`` (whole-branch review, Important #3):
    content-addressed output can't change for a given ``(blob_hash, kind)``,
    so an ``ok`` row is TERMINAL and is returned as-is. The calling step's own
    skip-if-ok check then short-circuits before doing any work, instead of
    this function itself resetting good, already-published output back to
    ``pending`` out from under a concurrent reader.

    Two workers racing the same ``(blob_hash, kind)`` INSERT (two files
    sharing a blob, each finishing their prior step near-simultaneously) can
    both miss the SELECT above and both attempt to INSERT; the loser's
    ``UNIQUE(blob_hash, kind)`` violation is caught here, rolled back, and
    re-SELECTed -- returning the winner's row either way, rather than
    surfacing a raw ``IntegrityError`` to the loser's job.
    """
    deriv = session.execute(
        select(Derivative).where(Derivative.blob_hash == blob_hash, Derivative.kind == kind)
    ).scalar_one_or_none()

    if deriv is None:
        deriv = Derivative(blob_hash=blob_hash, kind=kind, status=DerivativeStatus.PENDING)
        session.add(deriv)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            deriv = session.execute(
                select(Derivative).where(Derivative.blob_hash == blob_hash, Derivative.kind == kind)
            ).scalar_one()
        else:
            session.refresh(deriv)
        return deriv

    if deriv.status == DerivativeStatus.OK:
        return deriv

    deriv.status = DerivativeStatus.PENDING
    deriv.error = None
    session.commit()
    session.refresh(deriv)
    return deriv


def mark_derivative(
    session: Session,
    deriv: Derivative,
    *,
    status: DerivativeStatus,
    local_path: str | None = None,
    tool: str | None = None,
    error: str | None = None,
) -> None:
    """Set a derivative's terminal fields after a step attempt and commit.

    Refuses to downgrade an already-``ok`` row to anything else
    (whole-branch review, Important #3's "late-failure stomp"): a second run
    for the same shared blob that loses the race to finish first can still
    reach this call after the winner already published good output and
    marked the row ``ok`` -- overwriting it with ``failed``/``pending`` would
    make ``optimize_glb``/``render_thumb``'s "glb missing" checks lie about a
    status whose file is actually fine. Re-fetches the row's CURRENT state
    rather than trusting the possibly-stale ``deriv`` passed in, since another
    session may have committed the ``ok`` transition after this step's own
    ``upsert_derivative`` call returned. Upgrading ``failed`` -> ``ok`` (the
    whole point of a manual retry) is unaffected.
    """
    session.refresh(deriv)
    if deriv.status == DerivativeStatus.OK and status != DerivativeStatus.OK:
        logger.info(
            "mark_derivative: refusing to downgrade ok -> %s for blob_hash=%s kind=%s (self-healing"
            " race: a concurrent run already published good output for this shared blob)",
            status.value,
            deriv.blob_hash,
            deriv.kind.value,
        )
        return
    deriv.status = status
    deriv.local_path = local_path
    deriv.tool = tool
    deriv.error = error
    session.commit()


def upsert_assembly_thumb(session: Session, revision_id: int) -> AssemblyThumb:
    """Get-or-create the ``assembly_thumbs`` row for ``revision_id``,
    resetting it back to ``pending``/``error=None`` -- the per-revision
    mirror of ``upsert_derivative`` above (single row per revision instead of
    per ``(blob_hash, kind)``, since ``render_assembly_thumb`` "always
    re-renders": there's no idempotent-skip case that needs a prior terminal
    state left alone).
    """
    thumb = session.get(AssemblyThumb, revision_id)
    if thumb is None:
        thumb = AssemblyThumb(revision_id=revision_id, status=DerivativeStatus.PENDING)
        session.add(thumb)
    else:
        thumb.status = DerivativeStatus.PENDING
        thumb.error = None
    session.commit()
    session.refresh(thumb)
    return thumb


def mark_assembly_thumb(
    session: Session,
    revision_id: int,
    *,
    status: DerivativeStatus,
    local_path: str | None = None,
    error: str | None = None,
) -> None:
    """Set an assembly thumbnail's terminal fields after a render attempt
    and commit -- the per-revision mirror of ``mark_derivative`` above.
    Get-or-creates defensively (rather than assuming ``upsert_assembly_thumb``
    already ran) so a failure this function needs to record can never itself
    raise ``AttributeError`` on a missing row.
    """
    thumb = session.get(AssemblyThumb, revision_id)
    if thumb is None:
        thumb = AssemblyThumb(revision_id=revision_id)
        session.add(thumb)
    thumb.status = status
    thumb.local_path = local_path
    thumb.error = error
    session.commit()


def fetch_blob_to_temp(
    session: Session,
    settings: Settings,
    blob_hash: str,
    dest_dir: Path,
    suffix: str,
) -> Path:
    """Stream the first verified stored copy of ``blob_hash`` to
    ``dest_dir/blob{suffix}`` and return that path.

    ``suffix`` is the original file extension (e.g. ``".stl"``) -- tools like
    trimesh/lib3mf sniff format by file extension, not content, so the temp
    file must carry it even though the blob itself is addressed by hash.
    Picks the ``id``-ordered first ``File`` row for this blob with
    ``verified_at IS NOT NULL``; since a blob is content-addressed, any
    verified copy has identical bytes, so which one is picked doesn't matter
    beyond making the choice deterministic (Workstream C task C2: DOES matter
    for which BACKEND to read from, though -- resolved from that specific
    file's own ``backend_id`` via ``resolve_backend_for_file_sync``, not a
    caller-supplied default, since two verified copies of the same blob can
    live on two different backends). Raises ``LookupError`` if no verified
    copy exists (e.g. every file pointing at this blob is still mid-upload).
    """
    file = (
        session.execute(
            select(File)
            .where(File.blob_hash == blob_hash, File.verified_at.is_not(None))
            .order_by(File.id)
        )
        .scalars()
        .first()
    )
    if file is None:
        raise LookupError("no stored copy of blob")

    backend = resolve_backend_for_file_sync(session, settings, file)
    dest = dest_dir / f"blob{suffix}"
    with dest.open("wb") as fh:
        for chunk in backend.read(file.storage_path):
            fh.write(chunk)
    return dest
