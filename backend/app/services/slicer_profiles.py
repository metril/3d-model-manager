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
import logging
import re
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SlicerProfile
from app.models.enums import ProfileKind, ProfileSource
from app.virtual_printer.catalog import MODELS as _SSDP_MODELS

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
    meta: ProfileMeta | None = None


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
                meta=derive_profile_meta(
                    ProfileKind.PRINTER, merged, name=name, printer_model=model, nozzle_mm=nozzle
                ),
            )
        )

    def machine_lookup(mname: str) -> tuple[str | None, float | None] | None:
        if mname not in machines_by_name:
            return None
        return _printer_model_and_nozzle(_machine_lookup(mname, machines_by_name))

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
                    meta=derive_profile_meta(
                        kind,
                        merged,
                        name=name,
                        machine_lookup=machine_lookup,
                        printer_model=model,
                        nozzle_mm=nozzle,
                    ),
                )
            )

    return parsed


def upsert_profiles(
    session: Session,
    parsed: list[ParsedProfile],
    user_id: int | None,
    source: ProfileSource | None = None,
) -> dict:
    """Get-or-creates a `SlicerProfile` row per `(kind, name)` -- a
    re-import of the same bundle updates `data`/`printer_model`/`nozzle_mm`
    in place rather than duplicating. Returns `{"created", "updated",
    "skipped"}`.

    `source=ProfileSource.SYNCED` (sidecar sync): new rows are tagged
    `synced`; an existing row is only refreshed when it is itself `synced`
    -- imported or hand-made/edited rows are left alone and counted as
    `skipped`.
    """
    created = 0
    updated = 0
    skipped = 0
    for profile in parsed:
        meta = profile.meta or derive_profile_meta(
            profile.kind,
            profile.data,
            name=profile.name,
            printer_model=profile.printer_model,
            nozzle_mm=profile.nozzle_mm,
        )
        existing = session.execute(
            select(SlicerProfile).where(
                SlicerProfile.kind == profile.kind, SlicerProfile.name == profile.name
            )
        ).scalar_one_or_none()
        if existing is None:
            row = SlicerProfile(
                kind=profile.kind,
                name=profile.name,
                printer_model=profile.printer_model,
                nozzle_mm=profile.nozzle_mm,
                data=profile.data,
                created_by_user_id=user_id,
            )
            if source is not None:
                row.source = source
            apply_meta(row, meta)
            session.add(row)
            created += 1
        elif source is ProfileSource.SYNCED and existing.source is not ProfileSource.SYNCED:
            skipped += 1
        else:
            existing.printer_model = profile.printer_model
            existing.nozzle_mm = profile.nozzle_mm
            existing.data = profile.data
            apply_meta(existing, meta)
            updated += 1
    session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


def _norm_model(model: str | None) -> str:
    text = (model or "").lower().replace("bambu lab", "").replace("bambulab", "")
    return "".join(ch for ch in text if ch.isalnum())


# Alias table: normalised shorthand / SSDP code / firmware code -> canonical
# model key (the normalised product name). Merged from the SSDP catalogue
# (`virtual_printer.catalog.MODELS`) and the firmware lookup
# (`services.firmware.MODEL_TO_API_KEY`) plus common shorthand.
_MODEL_ALIASES: dict[str, str] = {
    "x1c": "x1carbon",
    "a1m": "a1mini",
    "n1": "a1mini",
    "n2s": "a1",
    "c11": "p1p",
    "c12": "p1s",
    "c13": "x1e",
    "n7": "p2s",
    "n6": "x2d",
    "n9": "a2l",
    "o1d": "h2d",
    "o1s": "h2s",
    "o1c": "h2c",
    "o1c2": "h2c",
    "o1e": "h2dpro",
    "o2d": "h2dpro",
    "blp001": "x1carbon",
    "blp002": "x1",
}
for _code, _product in _SSDP_MODELS.items():
    _MODEL_ALIASES.setdefault(_norm_model(_code), _norm_model(_product))
_KNOWN_MODEL_KEYS = frozenset(_MODEL_ALIASES.values()) | {
    "a1", "a1mini", "p1p", "p1s", "x1", "x1carbon", "x1e", "h2d", "h2s", "h2c", "p2s",
}  # fmt: skip


def model_key(text: str | None) -> str | None:
    """Canonical model key for any spelling of a printer model ("A1 mini",
    "Bambu Lab A1 mini", "A1M", "N1" -> "a1mini"). Matching is always key
    equality, never substring."""
    norm = _norm_model(text)
    if not norm:
        return None
    return _MODEL_ALIASES.get(norm, norm)


def usable_models(models: list[str | None]) -> list[str]:
    return [m for m in models if _norm_model(m)]


def filter_for_models(entries: list[dict], models: list[str | None]) -> list[dict]:
    """Keeps sidecar entries relevant to the configured printer `models`
    (compared ignoring case/spacing/the "Bambu Lab" prefix): printer
    presets of those models, plus process/filament presets whose
    `compatible_printers` names a kept printer. No usable models -> all
    entries (nothing to filter on)."""
    wanted = {_norm_model(m) for m in models if _norm_model(m)}
    wanted |= {_MODEL_ALIASES[w] for w in wanted if w in _MODEL_ALIASES}
    if not wanted:
        return entries
    kept_printers = {
        e["name"]
        for e in entries
        if e.get("kind") == "printer" and _norm_model(e.get("printer_model")) in wanted
    }
    return [
        e
        for e in entries
        if (e.get("kind") == "printer" and e["name"] in kept_printers)
        or (
            e.get("kind") != "printer"
            and kept_printers.intersection(e.get("compatible_printers") or [])
        )
    ]


# --- derived scope metadata -------------------------------------------------

NOZZLE_TOL = 0.01
LAYER_TOL = 0.005

_COMPAT_RE = re.compile(r"^(?P<model>.+?)\s+(?P<noz>\d+(?:\.\d+)?)\s*nozzle$", re.IGNORECASE)
_NAME_SUFFIX_RE = re.compile(
    r"@\s*(?:BBL\s+|Bambu\s*Lab\s+)?(?P<model>.+?)"
    r"(?:\s+(?P<noz>\d+(?:\.\d+)?)(?:\s*nozzle)?)?\s*$",
    re.IGNORECASE,
)
_LAYER_NAME_RE = re.compile(r"^(\d+\.\d+)mm")


@dataclass
class ProfileMeta:
    printer_model_key: str | None
    compat: list[dict] | None
    layer_height_mm: float | None


def _to_float(raw) -> float | None:
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _entry(key: str | None, nozzle: float | None) -> dict | None:
    return {"model_key": key, "nozzle_mm": nozzle} if key else None


def _parse_compatible(
    entries: list,
    machine_lookup: Callable[[str], tuple[str | None, float | None] | None] | None,
) -> list[dict]:
    out: list[dict] = []
    for raw in entries:
        text = str(raw)
        resolved = machine_lookup(text) if machine_lookup else None
        if resolved is not None and model_key(resolved[0]):
            e = _entry(model_key(resolved[0]), resolved[1])
        elif (m := _COMPAT_RE.match(text.strip())) is not None:
            e = _entry(model_key(m.group("model")), float(m.group("noz")))
        else:
            e = _entry(model_key(text), None)
        if e is not None and e not in out:
            out.append(e)
    return out


def _name_suffix_entry(name: str) -> dict | None:
    if "@" not in name:
        return None
    m = _NAME_SUFFIX_RE.search(name[name.rindex("@") :])
    if m is None:
        return None
    key = model_key(m.group("model"))
    # Without an explicit nozzle only a recognised model counts, so a suffix
    # like "@System" can't turn into a bogus model key.
    if key is None or (m.group("noz") is None and key not in _KNOWN_MODEL_KEYS):
        return None
    return _entry(key, float(m.group("noz")) if m.group("noz") else None)


def derive_profile_meta(
    kind: ProfileKind,
    data: dict,
    *,
    name: str,
    parent_lookup: Callable[[str], list[dict] | None] | None = None,
    machine_lookup: Callable[[str], tuple[str | None, float | None] | None] | None = None,
    printer_model: str | None = None,
    nozzle_mm: float | None = None,
) -> ProfileMeta:
    """Pure: scope metadata for a profile. `compat` is the full list of
    `{model_key, nozzle_mm|null}` entries it fits; `compat None` + no model
    key = universal ("any printer"). `parent_lookup(name)` returns an
    `inherits` parent's compat; `machine_lookup(name)` resolves a
    `compatible_printers` entry to its machine preset's `(printer_model,
    nozzle_mm)`. The `printer_model`/`nozzle_mm` columns are only a
    last-resort input."""
    data = data or {}
    compat: list[dict] | None = None

    listed = data.get("compatible_printers")
    if isinstance(listed, list) and listed:
        compat = _parse_compatible(listed, machine_lookup) or None
    if compat is None:
        model, noz = _printer_model_and_nozzle(data)
        e = _entry(model_key(model), noz)
        if e is not None:
            compat = [e]
    if compat is None and (parent := data.get("inherits")) and parent_lookup is not None:
        inherited = parent_lookup(str(parent))
        if inherited:
            compat = [dict(x) for x in inherited]
    if compat is None:
        e = _name_suffix_entry(name or str(data.get("name") or ""))
        if e is not None:
            compat = [e]
    if compat is None:
        e = _entry(model_key(printer_model), nozzle_mm)
        if e is not None:
            compat = [e]

    keys = {c["model_key"] for c in compat} if compat else set()
    layer = None
    if kind is ProfileKind.PROCESS:
        layer = _to_float(data.get("layer_height"))
        if layer is None and (m := _LAYER_NAME_RE.match(name or "")):
            layer = float(m.group(1))
    return ProfileMeta(
        printer_model_key=next(iter(keys)) if len(keys) == 1 else None,
        compat=compat,
        layer_height_mm=layer,
    )


def apply_meta(row: SlicerProfile, meta: ProfileMeta) -> None:
    """Writes the derived columns only -- never `printer_model`/`nozzle_mm`."""
    row.printer_model_key = meta.printer_model_key
    row.compat = meta.compat
    row.layer_height_mm = meta.layer_height_mm


def compat_entries(profile) -> list[dict] | None:
    if profile.compat:
        return list(profile.compat)
    if profile.printer_model_key:
        return [{"model_key": profile.printer_model_key, "nozzle_mm": None}]
    return None


def nozzle_fits(entry_nozzle: float | None, nozzle: float | None) -> bool:
    return entry_nozzle is None or nozzle is None or abs(entry_nozzle - nozzle) <= NOZZLE_TOL


def profile_in_scope(
    profile, printer_key: str | None, nozzle: float | None, layer: float | None = None
) -> bool:
    """Universal profiles pass the model/nozzle check; otherwise any `compat`
    entry must match the printer's model key and the nozzle (0.01 mm; NULL on
    either side = any). `layer` applies to process profiles only (0.005 mm)."""
    if layer is not None and profile.kind is ProfileKind.PROCESS:
        lh = profile.layer_height_mm
        if lh is None or abs(lh - layer) > LAYER_TOL:
            return False
    entries = compat_entries(profile)
    if entries is None or printer_key is None:
        return True
    return any(
        e["model_key"] == printer_key and nozzle_fits(e.get("nozzle_mm"), nozzle) for e in entries
    )


def scope_mismatch_reason(profile, printer_key: str | None, nozzle: float | None) -> str | None:
    """Why `profile` is out of model/nozzle scope (None when it fits)."""
    if profile_in_scope(profile, printer_key, nozzle):
        return None
    entries = compat_entries(profile) or []
    if not any(e["model_key"] == printer_key for e in entries):
        return f"for {profile.printer_model or entries[0]['model_key']}"
    nozzles = sorted(
        {e["nozzle_mm"] for e in entries if e["model_key"] == printer_key and e.get("nozzle_mm")}
    )
    return "for " + "/".join(f"{n:g}" for n in nozzles) + " mm nozzle"


def backfill_profile_meta(session: Session) -> int:
    """Derives scope metadata for rows never computed (`compat IS NULL AND
    layer_height_mm IS NULL`); idempotent. Writes derived columns only -- the
    user-editable `printer_model`/`nozzle_mm` are never touched. Returns the
    number of rows that gained metadata."""
    rows = (
        session.execute(
            select(SlicerProfile).where(
                SlicerProfile.compat.is_(None), SlicerProfile.layer_height_mm.is_(None)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    by_kind_name = {
        (r.kind, r.name): r for r in session.execute(select(SlicerProfile)).scalars().all()
    }

    def parent_for(kind: ProfileKind) -> Callable[[str], list[dict] | None]:
        def lookup(pname: str) -> list[dict] | None:
            parent = by_kind_name.get((kind, pname))
            return compat_entries(parent) if parent is not None else None

        return lookup

    def machine_lookup(mname: str) -> tuple[str | None, float | None] | None:
        m = by_kind_name.get((ProfileKind.PRINTER, mname))
        return (m.printer_model, m.nozzle_mm) if m is not None else None

    changed = 0
    # Printers first so process/filament rows can inherit their fresh compat.
    for row in sorted(rows, key=lambda r: r.kind is not ProfileKind.PRINTER):
        meta = derive_profile_meta(
            row.kind,
            row.data,
            name=row.name,
            parent_lookup=parent_for(row.kind),
            machine_lookup=machine_lookup,
            printer_model=row.printer_model,
            nozzle_mm=row.nozzle_mm,
        )
        apply_meta(row, meta)
        if meta.compat is not None or meta.layer_height_mm is not None:
            changed += 1
    session.commit()
    return changed


async def backfill_profile_meta_async(session) -> int:
    """Startup hook: `backfill_profile_meta` on an `AsyncSession`. Never fatal."""
    try:
        return await session.run_sync(backfill_profile_meta)
    except Exception:
        logging.getLogger(__name__).exception("profile scope backfill failed")
        return 0
