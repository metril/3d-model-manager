"""Sliced-3MF and gcode-header metadata extraction (SPEC pipeline row 1;
RESEARCH §3): a sliced Bambu Studio ``.gcode.3mf`` embeds its own per-plate
metadata as plain XML/JSON inside the zip, and a plate's ``.gcode`` embeds a
comment "HEADER_BLOCK" -- no third-party 3MF/gcode library needed, just
stdlib ``zipfile``/``xml.etree``/``json``.

Every field here is tolerant of its source being absent or a key being
missing: a partially-populated ``SlicedMeta``/``GcodeMeta`` (all fields
``None``/empty) beats a hard failure on an export from a different slicer
version or a manually-repackaged 3MF.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

SLICE_INFO_PATH = "Metadata/slice_info.config"
PROJECT_SETTINGS_PATH = "Metadata/project_settings.config"
MODEL_SETTINGS_PATH = "Metadata/model_settings.config"

# Comment lines look like "; key: value" or, packed onto one physical line,
# "; key one: value one; key two: value two" -- HEADER_BLOCK's first line
# (e.g. "model printing time: ...; total estimated time: ...") does the
# latter, so header parsing below splits each comment body on ";" before
# splitting each resulting segment on its first ":".
_COMMENT_LINE_RE = re.compile(r"^;\s*(.*)$")
_MAX_HEADER_COMMENT_LINES = 100

# "1h 1m 30s" / "55m 30s" / "30s" -> seconds. Every unit is optional; the
# match still succeeds (with all groups None) against a value with none of
# these units, which is treated as "couldn't parse" (returns None) below.
_DURATION_RE = re.compile(
    r"(?:(?P<days>\d+)d)?\s*(?:(?P<hours>\d+)h)?\s*(?:(?P<minutes>\d+)m)?\s*(?:(?P<seconds>\d+)s)?"
)


@dataclass(frozen=True, slots=True)
class SlicedMeta:
    """Parsed metadata for a sliced ``.gcode.3mf`` (SPEC ``blob_meta``'s
    sliced-file columns)."""

    print_time_s: int | None
    filament_g: float | None
    filament_m: float | None
    filament_types: list[str]
    layer_height: float | None
    nozzle: float | None
    printer_model: str | None
    plate_count: int
    plates: list[dict]


@dataclass(frozen=True, slots=True)
class GcodeMeta:
    """Parsed HEADER_BLOCK metadata for a bare ``.gcode`` file."""

    print_time_s: int | None
    filament_g: float | None
    filament_m: float | None
    layer_count: int | None
    max_z_mm: float | None
    raw: dict[str, str]


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


def parse_duration_s(text: str | None) -> int | None:
    """``"1h 23m 4s"``-style duration -> seconds, or ``None`` if unparseable.

    Public so ``app.pipeline.gcode_meta`` (R10-B: Prusa/Orca/Bambu comment
    metadata) can reuse the exact same ``XdXhXmXs`` grammar rather than
    duplicating the regex.
    """
    if not text:
        return None
    match = _DURATION_RE.search(text)
    if match is None or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


_parse_duration_s = parse_duration_s


def read_zip_member(zf: zipfile.ZipFile, name: str) -> bytes | None:
    """``zf.read(name)``, or ``None`` if ``name`` isn't present -- shared by
    every zip-embedded-metadata reader in this module (and, for
    ``model_settings.config``, by ``app.pipeline.thumbs``'s embedded-thumbnail
    resolution).
    """
    try:
        return zf.read(name)
    except KeyError:
        return None


def _plate_metadata(plate_el: ET.Element) -> dict[str, str | None]:
    return {
        key: m.get("value")
        for m in plate_el.findall("metadata")
        if (key := m.get("key")) is not None
    }


def _parse_slice_info(data: bytes | None) -> list[dict]:
    """Parse ``slice_info.config``'s per-plate ``<metadata>``/``<filament>``
    elements, in document order (not re-sorted by index).

    A plate whose ``index`` metadata is absent/unparseable falls back to its
    1-based position in document order (Important #1 fix): ``PlateOut.index``
    (``app.schemas.library``) is a non-optional ``int`` -- keeping the schema
    strict rather than loosening it to ``int | None`` -- so this parser must
    never hand back a plate with no usable index at all. Plates are otherwise
    unaddressable anyway (plate thumbs/gcode are keyed by index), so document
    position is the only meaningful fallback.
    """
    if data is None:
        return []
    try:
        root = ET.fromstring(data)  # noqa: S314 - our own worker-generated/trusted 3MF exports
    except ET.ParseError:
        return []

    plates = []
    for position, plate_el in enumerate(root.findall("plate"), start=1):
        meta = _plate_metadata(plate_el)
        filaments = [
            {
                "filament_id": _parse_int(filament_el.get("id")),
                "type": filament_el.get("type"),
                "color": filament_el.get("color"),
                "used_m": _parse_float(filament_el.get("used_m")),
                "used_g": _parse_float(filament_el.get("used_g")),
            }
            for filament_el in plate_el.findall("filament")
        ]
        index = _parse_int(meta.get("index"))
        plates.append(
            {
                "index": index if index is not None else position,
                "prediction_s": _parse_int(meta.get("prediction")),
                "weight_g": _parse_float(meta.get("weight")),
                "filaments": filaments,
            }
        )
    return plates


def parse_model_settings(data: bytes | None) -> dict[int, dict[str, str | None]]:
    """Parse ``model_settings.config``'s per-plate ``plater_id`` ->
    ``{gcode_file, thumbnail_file}`` mapping.

    Public (unlike this module's other parse helpers) because
    ``app.pipeline.thumbs``' ``extract_embedded_thumbs`` step reuses it
    directly to resolve per-plate thumbnail paths, independent of
    ``slice_info.config`` (which a plain, unsliced project ``3mf`` never
    has) -- see ``parse_gcode_3mf`` below for the sliced ``gcode_3mf`` case
    that joins this same mapping against ``slice_info.config``'s plates.
    """
    if data is None:
        return {}
    try:
        root = ET.fromstring(data)  # noqa: S314 - our own worker-generated/trusted 3MF exports
    except ET.ParseError:
        return {}

    result: dict[int, dict[str, str | None]] = {}
    for plate_el in root.findall("plate"):
        meta = _plate_metadata(plate_el)
        plater_id = _parse_int(meta.get("plater_id"))
        if plater_id is None:
            continue
        result[plater_id] = {
            "gcode_file": meta.get("gcode_file"),
            "thumbnail_file": meta.get("thumbnail_file"),
        }
    return result


def parse_gcode_3mf(path: Path) -> SlicedMeta:
    """Extract Bambu-Studio-flavored sliced metadata from a ``.gcode.3mf``
    (RESEARCH §3): ``slice_info.config`` for per-plate print-time/weight/
    filament use, ``project_settings.config`` for slicer-wide settings
    (printer model/nozzle/layer height/filament types), and
    ``model_settings.config`` for per-plate gcode/thumbnail file paths. Every
    source is independently optional -- a missing/renamed one just yields
    more ``None``/empty fields rather than raising.
    """
    with zipfile.ZipFile(path) as zf:
        slice_info = read_zip_member(zf, SLICE_INFO_PATH)
        project_settings_raw = read_zip_member(zf, PROJECT_SETTINGS_PATH)
        model_settings = read_zip_member(zf, MODEL_SETTINGS_PATH)

    plate_infos = _parse_slice_info(slice_info)
    plate_files = parse_model_settings(model_settings)

    project_settings: dict = {}
    if project_settings_raw is not None:
        try:
            project_settings = json.loads(project_settings_raw)
        except json.JSONDecodeError:
            project_settings = {}

    plates = []
    for info in plate_infos:
        index = info["index"]
        files = plate_files.get(index, {}) if index is not None else {}
        plates.append(
            {
                "index": index,
                "prediction_s": info["prediction_s"],
                "weight_g": info["weight_g"],
                "gcode_file": files.get("gcode_file"),
                "thumbnail_file": files.get("thumbnail_file"),
                "filaments": info["filaments"],
            }
        )

    predictions = [p["prediction_s"] for p in plates if p["prediction_s"] is not None]
    weights = [p["weight_g"] for p in plates if p["weight_g"] is not None]
    used_m_values = [f["used_m"] for p in plates for f in p["filaments"] if f["used_m"] is not None]

    filament_type = project_settings.get("filament_type")

    return SlicedMeta(
        print_time_s=sum(predictions) if predictions else None,
        filament_g=sum(weights) if weights else None,
        filament_m=sum(used_m_values) if used_m_values else None,
        filament_types=list(filament_type) if filament_type else [],
        layer_height=_parse_float(project_settings.get("layer_height")),
        nozzle=_parse_float(project_settings.get("printer_variant")),
        printer_model=project_settings.get("printer_model"),
        plate_count=len(plates),
        plates=plates,
    )


def _parse_header_comments(lines: list[str]) -> dict[str, str]:
    raw: dict[str, str] = {}
    for line in lines:
        match = _COMMENT_LINE_RE.match(line.strip())
        if match is None:
            continue
        body = match.group(1)
        for segment in body.split(";"):
            key, sep, value = segment.partition(":")
            if not sep:
                continue
            key = key.strip()
            value = value.strip()
            if key:
                raw[key] = value
    return raw


def parse_gcode_header(path: Path) -> GcodeMeta:
    """Extract the Bambu Studio HEADER_BLOCK from a bare ``.gcode`` file
    (RESEARCH §3): scans the first ``_MAX_HEADER_COMMENT_LINES`` comment
    lines (bounded so a multi-MB gcode body is never fully read) for known
    keys -- "total estimated time" (an ``XdXhXmXs`` duration), "total
    filament length [mm]" (converted to meters), "total filament weight
    [g]", "total layer number", "max_z_height". Every field is ``None`` when
    its key is absent.
    """
    comment_lines: list[str] = []
    with path.open("rb") as fh:
        for raw_line in fh:
            if len(comment_lines) >= _MAX_HEADER_COMMENT_LINES:
                break
            line = raw_line.decode("utf-8", errors="replace")
            if line.lstrip().startswith(";"):
                comment_lines.append(line)

    raw = _parse_header_comments(comment_lines)
    filament_mm = _parse_float(raw.get("total filament length [mm]"))

    return GcodeMeta(
        print_time_s=_parse_duration_s(raw.get("total estimated time")),
        filament_g=_parse_float(raw.get("total filament weight [g]")),
        filament_m=(filament_mm / 1000) if filament_mm is not None else None,
        layer_count=_parse_int(raw.get("total layer number")),
        max_z_mm=_parse_float(raw.get("max_z_height")),
        raw=raw,
    )
