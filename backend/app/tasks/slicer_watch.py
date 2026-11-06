"""``scan_slicer_watch`` Celery task (Round 8 Task 5: watched folder
auto-import). A companion entry point to ``POST /api/slicer/intake``
(Round 8 Task 4) for slicers -- Bambu Studio included -- that can't run a
post-processing script but CAN export finished sliced files straight into a
directory: point ``WATCH_DIR`` (``settings.watch_dir``) at
that folder and Celery beat polls it every ``WATCH_INTERVAL``
seconds, resolving each new file to a model exactly like the intake endpoint
does (``app.services.slicer_intake.resolve_and_attach_sync``).

**Redis singleton lock** (``SLICER_WATCH_LOCK_KEY``), mirroring
``app.tasks.scan``'s ``SCAN_LOCK_KEY``: a losing tick (the previous poll is
still running -- an unusually large drop, or a slow storage backend) just
returns immediately rather than racing a second pass over the same
directory. There is no tracking row to reclaim/mark on a lock loss (unlike
``ScanRun``/``Import``) -- an unprocessed file is simply picked up again on
the NEXT tick, so silently skipping this one is enough.

**Per-file isolation**: one bad file (unreadable, a DB hiccup, a storage
error) must never stall every other file sitting in the folder -- each
entry is staged/resolved/moved independently, and any exception is caught,
logged, and turned into a move to ``.failed/`` before moving on to the next
entry.

**Stability check**: a slicer can still be mid-write when a poll tick
lands (a large ``.gcode.3mf`` export takes real time to flush to disk). A
file whose mtime is younger than ``settings.watch_stable_s`` seconds
is left exactly where it is and reconsidered on a later tick -- imported
only once it's stopped changing. This gate (plus a ``_looks_like_temp_file``
skip for common sync-tool in-flight names) runs BEFORE extension
classification (M1 fix-review): the README recommends pointing a slicer's
export/SYNC target at this directory, and a sync tool typically stages an
in-flight file under a non-dot temp name that doesn't match any recognized
extension -- classifying first would quarantine that file to ``.failed/``
mid-write, out from under the tool, before it ever gets a chance to
stabilize.



**Terminal subdirectories**: ``.imported/`` and ``.failed/`` live INSIDE the
watched directory itself, so both the "skip dotfiles" rule and the
top-level-only ``iterdir()`` walk keep every future poll from ever touching
an entry that already landed in either one -- no separate bookkeeping (DB
row, sidecar file, ...) needed to remember what's already been handled.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import time
from pathlib import Path

from app.config import Settings, get_settings
from app.importers.download import stage_local_file
from app.models.enums import BlobFormat, BlobKind
from app.services import slicer_intake
from app.services.layout import infer_blob_kind_format
from app.services.redis_pool import get_sync_redis
from app.services.slicer_naming import _safe_basename
from app.services.storage_backends import resolve_default_backend_sync
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

SLICER_WATCH_LOCK_KEY = "tdmm:slicer-watch:lock"
_LOCK_TIMEOUT_S = 600

IMPORTED_DIRNAME = ".imported"
FAILED_DIRNAME = ".failed"


def _dedupe_move_target(dest_dir: Path, name: str) -> Path:
    """Same "copy conflict" convention as
    ``app.importers.archives._dedupe_member_name`` (``name (2).ext``,
    ``name (3).ext``, ...), just for a filesystem move target instead of a
    zip-member rel_path: two different watch-dir drops (e.g. re-exported
    after being manually restored from ``.failed/``) can share a basename,
    and a same-named prior arrival in ``dest_dir`` must never be silently
    clobbered.
    """
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _move_to(entry: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(exist_ok=True)
    target = _dedupe_move_target(dest_dir, entry.name)
    shutil.move(str(entry), str(target))


# Common in-flight naming conventions used by sync tools writing INTO the
# watched folder (the README recommends pointing a slicer's export/sync
# target at this path) -- a partial/temp name is skipped outright,
# regardless of its mtime, so a slow multi-GB transfer can never be
# classified/quarantined mid-write before the tool's own atomic rename to
# the final name lands. `.tmp`/`.part`/`.partial` cover rsync/generic sync
# tools; a leading/trailing `~` covers Syncthing's `~syncthing~Foo.gcode.3mf
# .tmp`-style naming (also caught by the `.tmp` suffix, but the `~` alone
# still applies once Syncthing strips the well-known suffix).
_TEMP_SUFFIXES = (".tmp", ".part", ".partial")


def _looks_like_temp_file(name: str) -> bool:
    return name.startswith("~") or name.endswith("~") or name.lower().endswith(_TEMP_SUFFIXES)


def _import_one(settings: Settings, entry: Path) -> None:
    """Stage ``entry`` to spool and resolve/attach it to a model. The
    staged spool file is cleaned up on any failure here (mirroring
    ``app.tasks.importing``'s per-file cleanup) -- on success it's left
    alone, since ``resolve_and_attach_sync`` has by then dispatched the
    ``store_to_backend`` job that owns consuming it.
    """
    staged = stage_local_file(settings, entry, rel_path=_safe_basename(entry.name))
    try:
        with base.sync_session() as s:
            backend, _default_backend_id = resolve_default_backend_sync(s, settings)
            slicer_intake.resolve_and_attach_sync(
                s, backend, settings, filename=entry.name, staged=staged
            )
    except Exception:
        staged.spool_path.unlink(missing_ok=True)
        raise


def _scan_once(settings: Settings, watch_dir: Path) -> None:
    imported_dir = watch_dir / IMPORTED_DIRNAME
    failed_dir = watch_dir / FAILED_DIRNAME

    for entry in sorted(watch_dir.iterdir(), key=lambda p: p.name):
        # Skips subdirectories (`.imported/`/`.failed/` included), any
        # dotfile -- a stray `.DS_Store`/editor swap file is quietly left
        # alone rather than logged as a failure -- and any recognizable
        # sync-tool in-flight temp name (see `_looks_like_temp_file`).
        if entry.is_dir() or entry.name.startswith(".") or _looks_like_temp_file(entry.name):
            continue

        # Stability gate BEFORE classification (M1 fix-review): a file
        # still being written has a fresh mtime regardless of what its
        # CURRENT (possibly not-yet-final) extension happens to be, so
        # checking this first keeps an in-flight write from ever being
        # misclassified as unsupported and yanked into `.failed/` out from
        # under the tool that's still writing it.
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            continue  # raced away between the iterdir() snapshot and here
        if time.time() - mtime < settings.watch_stable_s:
            continue  # still being written -- reconsidered on a later tick

        kind, format_ = infer_blob_kind_format(entry.name)
        if (kind is BlobKind.OTHER and format_ is BlobFormat.OTHER) or kind is BlobKind.DOC:
            logger.info(
                "slicer watch: unsupported file %r, moving to %s", entry.name, FAILED_DIRNAME
            )
            _move_to(entry, failed_dir)
            continue

        try:
            _import_one(settings, entry)
        except Exception:
            logger.exception("slicer watch: failed to import %r", entry.name)
            _move_to(entry, failed_dir)
            continue

        _move_to(entry, imported_dir)


@celery_app.task(name="app.tasks.slicer_watch.scan_slicer_watch")
def scan_slicer_watch() -> None:
    settings = get_settings()
    watch_dir = settings.watch_dir
    if watch_dir is None or not watch_dir.is_dir():
        return  # feature off, or the configured directory doesn't exist (yet)

    client = get_sync_redis(settings.redis_url)
    lock = client.lock(SLICER_WATCH_LOCK_KEY, timeout=_LOCK_TIMEOUT_S, blocking=False)
    if not lock.acquire(blocking=False):
        return  # another poll is still in flight; picked back up next tick
    try:
        _scan_once(settings, watch_dir)
    finally:
        with contextlib.suppress(Exception):
            lock.release()
