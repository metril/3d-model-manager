from __future__ import annotations

import io
import re
import zipfile

_PLATE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")


class NotSendableError(ValueError):
    """Not a startable sliced .gcode.3mf (bare .gcode, corrupt zip, or no plate gcode)."""


def plates_in_gcode_3mf(data: bytes) -> list[int]:
    """Sorted plate numbers whose gcode is PHYSICALLY present in the archive
    (RESEARCH §4: project_file needs Metadata/plate_N.gcode). Raises
    NotSendableError for a bare .gcode / non-zip / plateless archive."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise NotSendableError(
            "not a .gcode.3mf archive (bare .gcode is rejected for remote start)"
        ) from e
    plates = sorted(int(m.group(1)) for n in zf.namelist() if (m := _PLATE_RE.match(n)))
    if not plates:
        raise NotSendableError("archive contains no Metadata/plate_N.gcode; cannot start a print")
    return plates


def assert_plate_available(data: bytes, plate: int) -> None:
    plates = plates_in_gcode_3mf(data)
    if plate not in plates:
        raise NotSendableError(f"plate {plate} not in archive (available: {plates})")


def printable_objects(source, plate: int | None = None) -> list[dict]:
    """Printable objects listed in ``Metadata/slice_info.config`` as
    ``[{"id": identify_id, "name": str, "plate": int}]`` (skip-objects
    needs the ``identify_id``). ``source`` is a path or binary file object;
    ``plate`` limits to one plate. Never raises: unreadable -> ``[]``."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415 -- trusted local 3MF, stdlib parser

    try:
        with zipfile.ZipFile(source) as zf:
            root = ET.fromstring(zf.read("Metadata/slice_info.config"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return []
    out: list[dict] = []
    for pos, pl in enumerate(root.findall("plate"), start=1):
        index = pos
        for meta in pl.findall("metadata"):
            if meta.get("key") == "index" and (meta.get("value") or "").isdigit():
                index = int(meta.get("value"))
        if plate is not None and index != plate:
            continue
        for obj in pl.findall("object"):
            ident, name = obj.get("identify_id"), obj.get("name")
            if (
                ident
                and ident.isdigit()
                and name
                and (obj.get("skipped") or "false").lower() != "true"
            ):
                out.append({"id": int(ident), "name": name, "plate": index})
    return out
