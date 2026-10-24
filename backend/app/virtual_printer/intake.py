"""Hand an uploaded 3MF to the library and (per mode) the print queue.

Sync (worker-side world, ``app.tasks.base.sync_session``): the service calls
``ingest`` through ``asyncio.to_thread``. File resolution reuses
``services.slicer_intake`` (same naming/attach rules as Bambu Studio's
slicer intake endpoint).
"""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.config import Settings
from app.importers.download import stage_local_file
from app.models.enums import BlobFormat, QueueState, ReviewState, VirtualPrinterMode
from app.models.library import Blob, File, PrintQueueEntry
from app.services import billing, slicer_intake
from app.services.app_config import get_app_config_sync
from app.services.print_plan import estimate_item_sync
from app.services.server_config import ServerConfig, get_server_config_sync
from app.services.slicer_naming import _safe_basename
from app.services.storage_backends import resolve_default_backend_sync
from app.tasks import base

log = logging.getLogger("virtual_printer")


@dataclass(frozen=True)
class VpNetwork:
    """The virtual printer's listen network (the ``vp_*`` fields of the DB-backed
    ``ServerConfig``), frozen so ``wanted != current`` detects a change."""

    bind: str
    port_ssdp: int
    port_mqtt: int
    port_ftp: int
    pasv_min: int
    pasv_max: int
    pasv_address: str = ""  # "" = the control socket's local IP

    @classmethod
    def from_config(cls, cfg: ServerConfig) -> VpNetwork:
        return cls(
            bind=cfg.vp_bind,
            port_ssdp=cfg.vp_port_ssdp,
            port_mqtt=cfg.vp_port_mqtt,
            port_ftp=cfg.vp_port_ftp,
            pasv_min=cfg.vp_pasv_min,
            pasv_max=cfg.vp_pasv_max,
            pasv_address=cfg.vp_pasv_address or "",
        )


def load_network(settings: Settings | None = None) -> VpNetwork:
    with base.sync_session() as session:
        return VpNetwork.from_config(get_server_config_sync(session, settings))


@dataclass(frozen=True)
class VpConfig:
    id: int
    name: str
    model: str
    serial: str
    access_code: str = field(repr=False)
    mode: VirtualPrinterMode
    target_printer_id: int | None
    created_by_user_id: int | None


@dataclass(frozen=True)
class IngestResult:
    model_id: int
    file_id: int
    queue_entry_id: int | None


_PLATE_RE = re.compile(r"plate_(\d+)\.gcode", re.I)


def normalize_name(name: str) -> str:
    """Key used to match an FTP upload to its ``project_file`` command."""
    n = Path(name).name.lower()
    for suf in (".gcode.3mf", ".3mf", ".gcode"):
        if n.endswith(suf):
            return n[: -len(suf)]
    return n


def _sliced_name(path: Path) -> Path:
    """A sliced export uploaded as plain ``x.3mf`` (Studio does this) is
    renamed ``x.gcode.3mf`` so blob typing sees a sliced file."""
    if not path.name.lower().endswith(".3mf") or path.name.lower().endswith(".gcode.3mf"):
        return path
    try:
        with zipfile.ZipFile(path) as zf:
            if not any(_PLATE_RE.search(n) for n in zf.namelist() if n.lower().endswith(".gcode")):
                return path
    except (zipfile.BadZipFile, OSError):
        return path
    target = path.with_name(path.name[: -len(".3mf")] + ".gcode.3mf")
    path.rename(target)
    return target


def _plate(options: dict[str, Any]) -> int | None:
    m = _PLATE_RE.search(str(options.get("param") or ""))
    if m:
        return int(m.group(1))
    idx = options.get("plate_idx")
    return int(idx) if isinstance(idx, int) and idx > 0 else None


def ingest(
    settings: Settings, cfg: VpConfig, path: Path, options: dict[str, Any] | None
) -> IngestResult:
    """Store ``path`` in the library; when ``options`` (a ``project_file``
    command) is given and the mode isn't ``archive``, also queue it."""
    path = _sliced_name(path)
    try:
        return _ingest(settings, cfg, path, options)
    except Exception:
        path.unlink(missing_ok=True)  # may be the renamed path
        raise


def _ingest(
    settings: Settings, cfg: VpConfig, path: Path, options: dict[str, Any] | None
) -> IngestResult:
    staged = stage_local_file(settings, path, rel_path=_safe_basename(path.name))
    try:
        with base.sync_session() as s:
            backend, _ = resolve_default_backend_sync(s, settings)
            res = slicer_intake.resolve_and_attach_sync(
                s, backend, settings, filename=path.name, staged=staged
            )
            entry_id = None
            if options is not None and cfg.mode is not VirtualPrinterMode.ARCHIVE:
                entry_id = _enqueue(s, settings, cfg, res.model_id, res.file_id, options)
    except Exception:
        staged.spool_path.unlink(missing_ok=True)
        raise
    path.unlink(missing_ok=True)
    return IngestResult(res.model_id, res.file_id, entry_id)


def _enqueue(
    s, settings: Settings, cfg: VpConfig, model_id: int, file_id: int, options: dict[str, Any]
) -> int | None:
    file = s.get(File, file_id)
    blob = s.get(Blob, file.blob_hash) if file else None
    if blob is None or blob.format != BlobFormat.GCODE_3MF:
        return None  # not a sliced file: library only, nothing printable to queue
    mode = cfg.mode
    if mode is VirtualPrinterMode.PROXY and cfg.target_printer_id is None:
        log.warning("virtual printer %r: proxy target is gone; falling back to review", cfg.name)
        mode = VirtualPrinterMode.REVIEW
    proxy = mode is VirtualPrinterMode.PROXY
    position = (s.scalar(select(func.max(PrintQueueEntry.position))) or 0) + 1
    entry = PrintQueueEntry(
        model_id=model_id,
        file_id=file_id,
        sliced_file_id=file_id,
        printer_id=cfg.target_printer_id if proxy else None,
        plate=_plate(options),
        copies=1,
        state=QueueState.READY,
        position=position,
        review_state=(
            ReviewState.PENDING if mode is VirtualPrinterMode.REVIEW else ReviewState.NONE
        ),
        created_by_user_id=cfg.created_by_user_id,
    )
    # The slicer's AMS mapping refers to the virtual printer (no AMS), so it
    # is never stored: the dispatcher plans one against the real printer.
    s.add(entry)
    s.flush()
    uid = cfg.created_by_user_id
    app_cfg = get_app_config_sync(s, settings)
    if app_cfg.billing_enabled and uid is not None:
        cc = billing.ensure_private_center(s, uid)
        entry.cost_center_id = cc.id
        est = estimate_item_sync(s, settings, entry)
        billing.reserve(
            s, app_cfg, entry, Decimal(str(est)) if est is not None else None, user_id=uid
        )
    s.commit()
    return entry.id
