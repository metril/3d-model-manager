"""Which plate of a file is the real one. A sliced ``.gcode.3mf`` made for a
single plate N holds only ``Metadata/plate_N.gcode``, so plate numbers are
indices (``BlobMeta.raw["plates"][*]["index"]``), never ``1..plate_count``."""

from __future__ import annotations


def sliced_plate_indices(meta) -> list[int]:
    """Plate indices of a sliced file's metadata, in file order (empty if unknown)."""
    plates = [
        p
        for p in ((getattr(meta, "raw", None) or {}).get("plates")) or []
        if isinstance(p, dict) and isinstance(p.get("index"), int)
    ]
    # Prefer plates that really carry G-code (``gcode_file``) when the metadata records it.
    if any(p.get("gcode_file") for p in plates):
        plates = [p for p in plates if p.get("gcode_file")]
    return [p["index"] for p in plates]


def plate_indices(meta) -> list[int]:
    """Sliced indices, else the unsliced project's ``plates_json`` indices."""
    idx = sliced_plate_indices(meta)
    if idx:
        return idx
    plates = getattr(meta, "plates_json", None) or []
    return [p["index"] for p in plates if isinstance(p, dict) and isinstance(p.get("index"), int)]


def resolve_plate(meta, plate: int | None, fallback: int | None = None) -> int | None:
    """``plate`` if set, else the file's first sliced plate, else ``fallback``."""
    if plate is not None:
        return plate
    idx = sliced_plate_indices(meta) if meta is not None else []
    return idx[0] if idx else fallback
