"""Slicing with a 3MF's own process settings (``file_settings``): what the file
embeds, a per-blob cache of that, and the checks that make the option safe."""

from __future__ import annotations

import io
import zipfile
from collections import OrderedDict

import anyio
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Blob, File
from app.models.enums import BlobFormat
from app.models.slicing import SlicerProfile
from app.pipeline import plates3mf

_CACHE_MAX = 512
# blob hash -> embedded summary (``None`` = none). Blobs are content-addressed,
# so an entry never goes stale.
_cache: OrderedDict[str, dict | None] = OrderedDict()


def _remember(blob_hash: str, value: dict | None) -> None:
    _cache[blob_hash] = value
    _cache.move_to_end(blob_hash)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _read_ranged(backend, storage_path: str) -> dict | None:
    from app.services.derivatives import _RangedReader

    reader = io.BufferedReader(_RangedReader(backend, storage_path), buffer_size=1 << 16)
    try:
        with zipfile.ZipFile(reader) as zf:
            return plates3mf.read_embedded_process(zf)
    finally:
        reader.close()


async def embedded_for_file(
    db: AsyncSession, settings: Settings, file: File, blob: Blob
) -> dict | None:
    """The raw 3MF's embedded process summary; ``None`` for any other format or
    a 3MF without usable project settings. Cached by blob hash."""
    if blob.format != BlobFormat.THREEMF:
        return None
    if blob.hash in _cache:
        _cache.move_to_end(blob.hash)
        return _cache[blob.hash]
    from app.services.storage_backends import resolve_backend_for_file

    try:
        backend = await resolve_backend_for_file(db, settings, file)
        value = await anyio.to_thread.run_sync(_read_ranged, backend, file.storage_path)
    except Exception:  # best-effort; do not cache a transient storage failure
        return None
    _remember(blob.hash, value)
    return value


def unusable_reason(embedded: dict | None, printer_profile: SlicerProfile | None) -> str | None:
    """Why slicing with the file's settings on this recipe's printer would be
    wrong, or ``None``. Pure so the sync slice task shares it."""
    if embedded is None:
        return (
            "file_settings_unavailable: only a 3MF with project settings can keep its own settings"
        )
    file_nozzle = embedded.get("nozzle_mm")
    printer_nozzle = printer_profile.nozzle_mm if printer_profile is not None else None
    if (
        file_nozzle is not None
        and printer_nozzle is not None
        and abs(file_nozzle - printer_nozzle) > 1e-3
    ):
        return (
            f"file_settings_nozzle_mismatch: the file is set up for a {file_nozzle:g} mm nozzle "
            f"but the recipe's printer uses {printer_nozzle:g} mm"
        )
    return None


async def require_usable(
    db: AsyncSession,
    settings: Settings,
    file: File,
    printer_profile_id: int,
) -> None:
    """422 unless ``file`` is a 3MF with project settings whose nozzle matches
    the recipe's printer profile."""
    blob = await db.get(Blob, file.blob_hash)
    embedded = await embedded_for_file(db, settings, file, blob) if blob is not None else None
    reason = unusable_reason(embedded, await db.get(SlicerProfile, printer_profile_id))
    if reason is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, reason)
