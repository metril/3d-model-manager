"""Bambu Studio `BBL` profile bundle import (P6 "slicer, profiles, recipes
and dispatch" plan, Task 3). A bundle (directory or zip, Bambu Studio's
`system/BBL` layout) has a `BBL.json` manifest plus `machine/`, `process/`,
and `filament/` folders of JSON presets, each carrying its own `name` and
an optional `inherits` (the NAME of another preset in the SAME folder,
never across kinds). Only presets with `instantiation: "true"` are real,
selectable profiles -- the rest are abstract bases that exist purely to be
inherited from, and never themselves get imported.

`resolve()` flattens an `inherits` chain into one dict, child keys winning
over inherited ones. `parse_bundle()` walks the three folders, resolves
every instantiable preset, and works out each one's printer_model/nozzle
(straight off the printer preset itself; derived for process/filament via
their `compatible_printers` list, resolved back to printer presets -- only
when every compatible printer agrees on model+nozzle, else `None`).
`upsert_profiles()` then get-or-creates a `SlicerProfile` row per
`(kind, name)`, updating `data`/`printer_model`/`nozzle_mm` in place on a
re-import rather than duplicating.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SlicerProfile
from app.models.enums import ProfileKind

_KIND_BY_DIR = {
    "machine": ProfileKind.PRINTER,
    "process": ProfileKind.PROCESS,
    "filament": ProfileKind.FILAMENT,
}

# I7: a hostile/corrupt zip could otherwise either exhaust disk via a huge
# uncompressed payload (a "zip bomb") or exhaust inodes/time via an absurd
# member count -- both checked BEFORE `extractall`, off the archive's own
# directory (no decompression needed to read `file_size`/count members).
_MAX_ZIP_MEMBERS = 5000
_MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class ProfileImportError(ValueError):
    """The bundle is malformed: a broken `inherits` chain (missing parent or
    a cycle), or a zip whose members try to escape the extraction dir."""


def resolve(name: str, by_name: dict[str, dict], _seen: frozenset[str] = frozenset()) -> dict:
    """Flattens `name`'s `inherits` chain within `by_name` (one kind-folder's
    worth of presets) into a single dict, child keys winning over inherited
    ones, with no `inherits` key left in the result."""
    if name in _seen:
        raise ProfileImportError(f"inherits cycle at {name!r}")
    raw = by_name.get(name)
    if raw is None:
        raise ProfileImportError(f"missing parent {name!r}")
    parent = raw.get("inherits")
    base = resolve(parent, by_name, _seen | {name}) if parent else {}
    merged = {**base, **raw}
    merged.pop("inherits", None)
    return merged


@dataclass
class ParsedProfile:
    kind: ProfileKind
    name: str
    printer_model: str | None
    nozzle_mm: float | None
    data: dict


def _is_instantiable(raw: dict) -> bool:
    value = raw.get("instantiation", "false")
    return str(value).lower() == "true"


def _load_folder(root: Path, dirname: str) -> dict[str, dict]:
    by_name: dict[str, dict] = {}
    folder = root / dirname
    if not folder.is_dir():
        return by_name
    for path in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ProfileImportError(f"{path.name!r} is not valid JSON: {exc}") from exc
        name = raw.get("name", path.stem)
        by_name[name] = raw
    return by_name


def _machine_lookup(name: str, by_name: dict[str, dict]) -> dict:
    return resolve(name, by_name)


def _printer_model_and_nozzle(merged: dict) -> tuple[str | None, float | None]:
    printer_model = merged.get("printer_model")
    nozzle_raw = merged.get("nozzle_diameter")
    nozzle_mm = None
    if nozzle_raw:
        try:
            nozzle_mm = float(nozzle_raw[0] if isinstance(nozzle_raw, list) else nozzle_raw)
        except (TypeError, ValueError):
            nozzle_mm = None
    return printer_model, nozzle_mm


def _compatible_printer_model_and_nozzle(
    merged: dict, machines_by_name: dict[str, dict]
) -> tuple[str | None, float | None]:
    """Resolves a process/filament preset's `compatible_printers` back to
    machine presets, returning (printer_model, nozzle_mm) only when every
    compatible printer agrees on exactly one distinct pair; `(None, None)`
    otherwise (no compatible printers, or they span more than one model/
    nozzle combination)."""
    compatible = merged.get("compatible_printers") or []
    pairs: set[tuple[str | None, float | None]] = set()
    for printer_name in compatible:
        if printer_name not in machines_by_name:
            continue
        machine_merged = _machine_lookup(printer_name, machines_by_name)
        pairs.add(_printer_model_and_nozzle(machine_merged))
    if len(pairs) == 1:
        return next(iter(pairs))
    return None, None


def _extract_zip(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        infolist = zf.infolist()
        if len(infolist) > _MAX_ZIP_MEMBERS:
            raise ProfileImportError(
                f"zip has too many members ({len(infolist)} > {_MAX_ZIP_MEMBERS})"
            )
        total_size = sum(member.file_size for member in infolist)
        if total_size > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ProfileImportError(
                f"zip's uncompressed contents are too large "
                f"({total_size} > {_MAX_ZIP_UNCOMPRESSED_BYTES} bytes)"
            )
        for member in infolist:
            name = member.filename
            dest_resolved = dest.resolve()
            member_path = (dest / name).resolve()
            escapes = member_path != dest_resolved and not str(member_path).startswith(
                str(dest_resolved) + "/"
            )
            if escapes:
                raise ProfileImportError(f"zip member escapes bundle root: {name!r}")
            if ".." in Path(name).parts:
                raise ProfileImportError(f"zip member escapes bundle root: {name!r}")
            # Reject symlinks (upper 4 bits of external_attr's mode == 0xA).
            mode = member.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise ProfileImportError(f"zip member is a symlink: {name!r}")
        zf.extractall(dest)


def parse_bundle(root: Path, printer_model: str | None = None) -> list[ParsedProfile]:
    """Parses a BBL bundle (directory, or a zip -- extracted to a tempdir
    that's always cleaned up afterwards, I7) into one `ParsedProfile` per
    instantiable preset. `printer_model`, when given, keeps only machine
    presets of that model plus process/filament presets compatible with at
    least one of them."""
    root = Path(root)
    if root.is_file():
        with tempfile.TemporaryDirectory(prefix="slicer-bundle-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            _extract_zip(root, tmp_path)
            return _parse_bundle_dir(tmp_path, printer_model)
    return _parse_bundle_dir(root, printer_model)


def _parse_bundle_dir(root: Path, printer_model: str | None) -> list[ParsedProfile]:
    machines_by_name = _load_folder(root, "machine")
    processes_by_name = _load_folder(root, "process")
    filaments_by_name = _load_folder(root, "filament")

    parsed: list[ParsedProfile] = []
    kept_machine_names: set[str] = set()

    for name, raw in machines_by_name.items():
        if not _is_instantiable(raw):
            continue
        merged = resolve(name, machines_by_name)
        model, nozzle = _printer_model_and_nozzle(merged)
        if printer_model is not None and model != printer_model:
            continue
        kept_machine_names.add(name)
        parsed.append(
            ParsedProfile(
                kind=ProfileKind.PRINTER,
                name=name,
                printer_model=model,
                nozzle_mm=nozzle,
                data=merged,
            )
        )

    for _dirname, by_name, kind in (
        ("process", processes_by_name, ProfileKind.PROCESS),
        ("filament", filaments_by_name, ProfileKind.FILAMENT),
    ):
        for name, raw in by_name.items():
            if not _is_instantiable(raw):
                continue
            merged = resolve(name, by_name)
            model, nozzle = _compatible_printer_model_and_nozzle(merged, machines_by_name)
            if printer_model is not None:
                compatible = merged.get("compatible_printers") or []
                if not (set(compatible) & kept_machine_names):
                    continue
            parsed.append(
                ParsedProfile(
                    kind=kind,
                    name=name,
                    printer_model=model,
                    nozzle_mm=nozzle,
                    data=merged,
                )
            )

    return parsed


def upsert_profiles(session: Session, parsed: list[ParsedProfile], user_id: int | None) -> dict:
    """Get-or-creates a `SlicerProfile` row per `(kind, name)` -- a
    re-import of the same bundle updates `data`/`printer_model`/`nozzle_mm`
    in place rather than duplicating. Returns `{"created": n, "updated": n}`.
    """
    created = 0
    updated = 0
    for profile in parsed:
        existing = session.execute(
            select(SlicerProfile).where(
                SlicerProfile.kind == profile.kind, SlicerProfile.name == profile.name
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                SlicerProfile(
                    kind=profile.kind,
                    name=profile.name,
                    printer_model=profile.printer_model,
                    nozzle_mm=profile.nozzle_mm,
                    data=profile.data,
                    created_by_user_id=user_id,
                )
            )
            created += 1
        else:
            existing.printer_model = profile.printer_model
            existing.nozzle_mm = profile.nozzle_mm
            existing.data = profile.data
            updated += 1
    session.commit()
    return {"created": created, "updated": updated}
