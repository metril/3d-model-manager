"""Cloud preset sync: pull a user's Orca Cloud / Bambu Cloud printer, process
and filament presets into ``slicer_profiles`` rows, with resync and diff.

A synced row carries ``cloud_source`` / ``cloud_id`` plus two hashes taken at
the last sync -- ``cloud_hash`` (of the remote content) and ``data_hash`` (of
the stored ``data``) -- so a resync can tell "changed upstream" from "edited
locally" and never silently overwrites local edits:

    new              no local row yet
    up_to_date       neither side changed
    remote_changed   upstream changed, local untouched   -> resync updates it
    local_modified   edited locally, upstream unchanged  -> skipped unless overwrite
    conflict         both changed                        -> skipped unless overwrite

Cloud presets are stored as pulled (an ``inherits`` parent is NOT flattened:
the slicer sidecar resolves it against its bundled presets at slice time);
only ``type`` / ``from`` are normalised to what the slicer CLI accepts.
Bambu Cloud's public (bundled) presets are not synced -- the sidecar already
ships those; only the account's private (custom) presets are.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SlicerProfile
from app.models.enums import ProfileKind, ProfileSource
from app.services import bambu_auth

SOURCES = ("orca", "bambu")

NEW = "new"
UP_TO_DATE = "up_to_date"
REMOTE_CHANGED = "remote_changed"
LOCAL_MODIFIED = "local_modified"
CONFLICT = "conflict"

# Orca's `content.type` vocabulary (and the BambuStudio-style aliases) -> kind.
_ORCA_TYPES = {
    "printer": ProfileKind.PRINTER,
    "machine": ProfileKind.PRINTER,
    "print": ProfileKind.PROCESS,
    "process": ProfileKind.PROCESS,
    "filament": ProfileKind.FILAMENT,
}
# Bambu's slicer-settings response keys -> kind.
_BAMBU_KEYS = {
    "printer": ProfileKind.PRINTER,
    "print": ProfileKind.PROCESS,
    "filament": ProfileKind.FILAMENT,
}
# What the slicer CLI's `--load-settings` parser accepts for `type`.
_CLI_TYPE = {
    ProfileKind.PRINTER: "machine",
    ProfileKind.PROCESS: "process",
    ProfileKind.FILAMENT: "filament",
}
_BAMBU_SLICER_VERSION = "1.0.0.0"


class CloudPresetError(Exception):
    """A cloud call failed (network / bad response)."""


@dataclass
class RemotePreset:
    source: str
    preset_id: str
    kind: ProfileKind
    name: str
    updated_time: str | None = None
    content: dict | None = None  # None until fetched (Bambu lists metadata only)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


def content_hash(content: dict) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def normalize(kind: ProfileKind, name: str, content: dict) -> dict:
    """The stored ``data``: the cloud content with the keys the slicer CLI
    insists on (``type``, ``from``, ``name``) pinned."""
    return {
        **content,
        "type": _CLI_TYPE[kind],
        "from": "system",
        "name": content.get("name") or name,
    }


def status_of(row: SlicerProfile | None, remote_hash: str) -> str:
    if row is None:
        return NEW
    remote_changed = row.cloud_hash != remote_hash
    local_modified = row.data_hash != content_hash(row.data or {})
    if remote_changed and local_modified:
        return CONFLICT
    if remote_changed:
        return REMOTE_CHANGED
    if local_modified:
        return LOCAL_MODIFIED
    return UP_TO_DATE


def diff_data(local: dict, remote: dict) -> list[dict]:
    """Top-level key differences, ``local`` -> ``remote``."""
    out = []
    for key in sorted(set(local) | set(remote)):
        a, b = local.get(key), remote.get(key)
        if a != b:
            out.append({"key": key, "local": a, "remote": b})
    return out


def nozzle_of(content: dict) -> float | None:
    raw = content.get("nozzle_diameter")
    try:
        return float(raw[0] if isinstance(raw, list) and raw else raw)
    except (TypeError, ValueError):
        return None


# --- remote fetch (blocking; callers run these in a worker thread) ---------


def parse_orca(profiles: list[dict]) -> list[RemotePreset]:
    out = []
    for entry in profiles:
        content = entry.get("content") if isinstance(entry, dict) else None
        if not isinstance(content, dict) or entry.get("id") is None:
            continue
        kind = _ORCA_TYPES.get(str(content.get("type", "")))
        if kind is None:
            continue
        upd = entry.get("updated_time")
        out.append(
            RemotePreset(
                source="orca",
                preset_id=str(entry["id"]),
                kind=kind,
                name=str(entry.get("name") or content.get("name") or entry["id"]),
                updated_time=str(upd) if upd is not None else None,
                content=content,
            )
        )
    return out


def _bambu_get(session: Session, settings: Settings, path: str, params: dict) -> Any:
    token = bambu_auth.get_access_token_sync(session, settings)
    region = bambu_auth.get_bambu_auth_sync(session, settings).region
    try:
        with bambu_auth._client(region) as c:  # noqa: SLF001 -- the shared httpx seam
            r = c.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise CloudPresetError(f"Bambu Cloud request failed: {exc}") from exc
    if r.status_code == 401:
        raise bambu_auth.BambuAuthError(
            "Bambu Cloud session expired; reconnect your account.", kind="expired"
        )
    if r.status_code >= 400:
        raise CloudPresetError(f"Bambu Cloud returned {r.status_code}: {r.text[:200]}")
    return r.json()


def fetch_bambu_list(session: Session, settings: Settings) -> list[RemotePreset]:
    data = _bambu_get(
        session, settings, "/iot-service/api/slicer/setting", {"version": _BAMBU_SLICER_VERSION}
    )
    out = []
    for api_key, kind in _BAMBU_KEYS.items():
        for s in ((data.get(api_key) or {}).get("private") or []) if isinstance(data, dict) else []:
            pid = s.get("setting_id") or s.get("id")
            if not pid:
                continue
            out.append(
                RemotePreset(
                    source="bambu",
                    preset_id=str(pid),
                    kind=kind,
                    name=str(s.get("name") or pid),
                    updated_time=str(s["updated_time"]) if s.get("updated_time") else None,
                )
            )
    return out


def fetch_bambu_content(session: Session, settings: Settings, preset_id: str) -> dict:
    data = _bambu_get(
        session,
        settings,
        f"/iot-service/api/slicer/setting/{preset_id}",
        {"version": _BAMBU_SLICER_VERSION},
    )
    content = data.get("setting") if isinstance(data, dict) else None
    if not isinstance(content, dict):
        content = data if isinstance(data, dict) else {}
    return content


# --- local rows ------------------------------------------------------------


def local_rows(session: Session, source: str) -> dict[str, SlicerProfile]:
    rows = session.scalars(select(SlicerProfile).where(SlicerProfile.cloud_source == source)).all()
    return {r.cloud_id: r for r in rows if r.cloud_id}


def _unique_name(session: Session, kind: ProfileKind, name: str, source: str) -> str:
    """``name`` unless a different profile already holds ``(kind, name)``; then
    ``name (Orca Cloud)`` / ``(Bambu Cloud)`` (and a counter if still taken)."""
    label = "Orca Cloud" if source == "orca" else "Bambu Cloud"
    candidate, n = name, 1
    while session.scalar(
        select(SlicerProfile.id).where(SlicerProfile.kind == kind, SlicerProfile.name == candidate)
    ):
        candidate = f"{name} ({label})" if n == 1 else f"{name} ({label} {n})"
        n += 1
    return candidate


def _inherited_model(
    session: Session, kind: ProfileKind, content: dict
) -> tuple[str | None, float | None]:
    parent = content.get("inherits")
    if not parent:
        return None, None
    row = session.scalar(
        select(SlicerProfile).where(SlicerProfile.kind == kind, SlicerProfile.name == str(parent))
    )
    return (row.printer_model, row.nozzle_mm) if row is not None else (None, None)


def upsert_remote(
    session: Session,
    remote: RemotePreset,
    content: dict,
    user_id: int | None,
    *,
    overwrite_local: bool = False,
) -> str:
    """``_upsert_once`` that survives a concurrent sync inserting the same
    preset (unique ``cloud_id`` / name): roll back the savepoint and retry,
    which then sees the winner's row and takes the update path."""
    for attempt in range(3):
        try:
            with session.begin_nested():
                return _upsert_once(
                    session, remote, content, user_id, overwrite_local=overwrite_local
                )
        except IntegrityError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def _upsert_once(
    session: Session,
    remote: RemotePreset,
    content: dict,
    user_id: int | None,
    *,
    overwrite_local: bool = False,
) -> str:
    """Create or refresh the row for ``remote``; returns ``created`` /
    ``updated`` / the skip reason (``local_modified`` / ``conflict`` /
    ``up_to_date``)."""
    rows = local_rows(session, remote.source)
    row = rows.get(remote.preset_id)
    remote_hash = content_hash(content)
    st = status_of(row, remote_hash)
    if st == UP_TO_DATE:
        return UP_TO_DATE
    if st in (LOCAL_MODIFIED, CONFLICT) and not overwrite_local:
        return st

    data = normalize(remote.kind, remote.name, content)
    model = content.get("printer_model") if remote.kind is ProfileKind.PRINTER else None
    nozzle = nozzle_of(content) if remote.kind is ProfileKind.PRINTER else None
    if model is None and nozzle is None:
        model, nozzle = _inherited_model(session, remote.kind, content)
    now = datetime.now(UTC)
    if row is None:
        session.add(
            SlicerProfile(
                kind=remote.kind,
                name=_unique_name(session, remote.kind, remote.name, remote.source),
                printer_model=model,
                nozzle_mm=nozzle,
                data=data,
                source=ProfileSource.IMPORT,
                cloud_source=remote.source,
                cloud_id=remote.preset_id,
                cloud_hash=remote_hash,
                cloud_updated=remote.updated_time,
                data_hash=content_hash(data),
                cloud_synced_at=now,
                created_by_user_id=user_id,
            )
        )
        session.flush()
        return "created"
    row.printer_model = model
    row.nozzle_mm = nozzle
    row.data = data
    row.cloud_hash = remote_hash
    row.cloud_updated = remote.updated_time
    row.data_hash = content_hash(data)
    row.cloud_synced_at = now
    session.flush()
    return "updated"
