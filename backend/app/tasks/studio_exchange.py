"""``scan_studio_exchange``: ingest files Bambu Studio saved under
``<studio_exchange_dir>/<token>/`` back into the model/revision the session
was opened from, and sweep expired session directories.

Mirrors ``app.tasks.slicer_watch`` (mtime-stability gate, temp-name skip,
``.imported/``/``.failed/`` terminal dirs, Redis singleton lock, per-file
isolation) but resolves the destination from the Redis session record
(``tdmm:studio:session:<token>``) instead of the filename. ``<token>/in/``
(the input copy) and dot-dirs are never scanned. A sliced ``.gcode.3mf``
needs no special marking: ``layout.infer_blob_kind_format`` classifies it
as sliced by name, exactly as for every other ingest path.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path

from redis import Redis
from sqlalchemy import select

from app.config import Settings, get_settings
from app.importers.download import stage_local_file
from app.models.enums import BlobFormat, BlobKind
from app.models.library import File, Model, Revision
from app.services import events, library
from app.services.layout import infer_blob_kind_format
from app.services.server_config import current_sync
from app.services.slicer_naming import _safe_basename
from app.services.studio_sessions import SESSION_TTL_S, session_key
from app.tasks import base
from app.tasks.celery_app import celery_app
from app.tasks.slicer_watch import (
    FAILED_DIRNAME,
    IMPORTED_DIRNAME,
    _looks_like_temp_file,
    _move_to,
)

logger = logging.getLogger(__name__)

STUDIO_SCAN_LOCK_KEY = "tdmm:studio:scan:lock"
_LOCK_TIMEOUT_S = 600
INPUT_DIRNAME = "in"


def _return_files(token_dir: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(token_dir):
        at_top = Path(dirpath) == token_dir
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and not (at_top and d == INPUT_DIRNAME)
        )
        out.extend(Path(dirpath) / f for f in sorted(filenames))
    return out


def _unique_rel_path(taken: set[str], name: str) -> str:
    stem, suffix = (
        (name[:-10], name[-10:])
        if name.lower().endswith(".gcode.3mf")
        else (
            Path(name).stem,
            Path(name).suffix,
        )
    )
    candidate, n = name, 1
    while candidate in taken:
        candidate = f"{stem} (studio){suffix}" if n == 1 else f"{stem} (studio {n}){suffix}"
        n += 1
    return candidate


def _import_one_studio(settings: Settings, entry: Path, rec: dict) -> dict | None:
    """Attach ``entry`` to the session's revision. Returns the event payload,
    or ``None`` when identical bytes are already on that revision."""
    name = _safe_basename(entry.name)
    kind, format_ = infer_blob_kind_format(name)
    if (kind is BlobKind.OTHER and format_ is BlobFormat.OTHER) or kind is BlobKind.DOC:
        raise ValueError(f"unsupported file {name!r}")
    staged = stage_local_file(settings, entry, rel_path=name)
    try:
        with base.sync_session() as s:
            model = s.get(Model, rec["model_id"])
            revision = s.get(Revision, rec["revision_id"])
            if model is None or model.deleted_at is not None or revision is None:
                raise LookupError("session target model/revision is gone")
            rows = s.execute(
                select(File.rel_path, File.blob_hash).where(File.revision_id == revision.id)
            ).all()
            if any(h == staged.blob_hash for _, h in rows):
                staged.spool_path.unlink(missing_ok=True)
                return None
            staged = replace(staged, rel_path=_unique_rel_path({p for p, _ in rows}, name))
            file = library.store_imported_file_sync(
                s, model=model, revision=revision, staged=staged
            )
            return {
                "type": "studio.file_returned",
                "model_slug": model.slug,
                "model_id": model.id,
                "file_id": file.id,
                "name": staged.rel_path,
                "sliced": name.lower().endswith(".gcode.3mf"),
            }
    except Exception:
        staged.spool_path.unlink(missing_ok=True)
        raise


def _scan_once(settings: Settings, client: Redis) -> None:
    root = settings.studio_exchange_dir
    now = time.time()
    for token_dir in sorted(root.iterdir(), key=lambda p: p.name):
        if not token_dir.is_dir() or token_dir.name.startswith("."):
            continue
        try:
            if now - token_dir.stat().st_mtime > SESSION_TTL_S:
                shutil.rmtree(token_dir, ignore_errors=True)  # sweeper
                continue
        except FileNotFoundError:
            continue
        for entry in _return_files(token_dir):
            if entry.name.startswith(".") or _looks_like_temp_file(entry.name):
                continue
            try:
                if now - entry.stat().st_mtime < settings.watch_stable_s:
                    continue  # still being written
            except FileNotFoundError:
                continue
            raw = client.get(session_key(token_dir.name))
            if raw is None:
                logger.warning("studio exchange: no session for %r, quarantining", entry)
                _move_to(entry, token_dir / FAILED_DIRNAME)
                continue
            try:
                payload = _import_one_studio(settings, entry, json.loads(raw))
            except Exception:
                logger.exception("studio exchange: failed to import %r", entry)
                _move_to(entry, token_dir / FAILED_DIRNAME)
                continue
            if payload is not None:
                events.publish_event_sync(settings.redis_url, payload)
            _move_to(entry, token_dir / IMPORTED_DIRNAME)


@celery_app.task(name="app.tasks.studio_exchange.scan_studio_exchange")
def scan_studio_exchange() -> None:
    settings = get_settings()
    root = settings.studio_exchange_dir
    if not current_sync(settings).studio_enabled or not root.is_dir():
        return
    client = Redis.from_url(settings.redis_url)
    try:
        lock = client.lock(STUDIO_SCAN_LOCK_KEY, timeout=_LOCK_TIMEOUT_S, blocking=False)
        if not lock.acquire(blocking=False):
            return
        try:
            _scan_once(settings, client)
        finally:
            with contextlib.suppress(Exception):
                lock.release()
    finally:
        with contextlib.suppress(Exception):
            client.close()
