"""Automatic camera snapshot on print job completion."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import httpx
from blake3 import blake3
from sqlalchemy.orm import Session as SyncSession

from app.config import Settings
from app.importers.download import StagedFile
from app.models.enums import BlobFormat, BlobKind
from app.models.library import File, Model, Revision
from app.models.printing import PrintJob
from app.printers.base import PrinterAdapter
from app.services import library, spool

log = logging.getLogger(__name__)

MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024


def capture_and_save_finish_snapshot(
    settings: Settings,
    session: SyncSession,
    job: PrintJob,
    adapter: PrinterAdapter,
) -> None:
    """Capture camera snapshot when print finishes and attach as photo to model revision."""
    try:
        cam = adapter.get_camera_urls()
        snapshot_url = cam.get("snapshot_url")
        if not snapshot_url:
            return

        buf = bytearray()
        with (
            httpx.Client(timeout=6.0, follow_redirects=False) as client,
            client.stream("GET", snapshot_url) as resp,
        ):
            if not resp.is_success:
                log.warning("Snapshot request failed with status %s", resp.status_code)
                return
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_SNAPSHOT_BYTES:
                    log.warning("Snapshot for job %s exceeds %d bytes", job.id, MAX_SNAPSHOT_BYTES)
                    return
        if not buf:
            log.warning("Snapshot request returned an empty body")
            return
        img_bytes = bytes(buf)

        file = session.get(File, job.file_id)
        if file is None or file.revision_id is None:
            return
        revision = session.get(Revision, file.revision_id)
        if revision is None:
            return
        model = session.get(Model, revision.model_id)
        if model is None:
            return

        token = uuid.uuid4()
        spool.ensure_spool_dir(settings)
        spool_path = spool.spool_path(settings, token)
        spool_path.write_bytes(img_bytes)

        h = blake3(img_bytes).hexdigest()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rel_name = f"print-result-{timestamp}.jpg"

        staged = StagedFile(
            token=token,
            spool_path=spool_path,
            blob_hash=h,
            size=len(img_bytes),
            rel_path=rel_name,
            kind=BlobKind.IMAGE,
            format_=BlobFormat.JPG,
        )

        # Savepoint: a failed store rolls back only itself, not the caller's
        # pending job-state update.
        with session.begin_nested():
            library.store_imported_file_sync(session, model=model, revision=revision, staged=staged)
        log.info("Captured finish snapshot for job %s: saved as %s", job.id, rel_name)
    except Exception as exc:
        log.warning("Could not capture finish snapshot for job %s: %s", job.id, exc)
