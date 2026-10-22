"""A tiny Bambu Studio `system/BBL`-shaped profile bundle for
`test_slicer_profiles.py`: `BBL.json` plus `machine/`, `process/`,
`filament/` folders, two printer models (A1 mini, X1 Carbon) so the
printer_model filter has something to actually filter, an `inherits`
chain three levels deep for filament, and one abstract (`instantiation:
false`) preset per kind that must never show up in `parse_bundle`'s output.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

BBL_JSON = {"name": "BBL", "version": "01.09.00.00", "model_id": ["BL-P001", "BL-P002"]}

MACHINES = {
    # Abstract base -- never instantiated directly.
    "fdm_bbl_3dp_001_common": {
        "name": "fdm_bbl_3dp_001_common",
        "instantiation": "false",
        "nozzle_diameter": ["0.4"],
    },
    "Bambu Lab A1 mini 0.4 nozzle": {
        "name": "Bambu Lab A1 mini 0.4 nozzle",
        "inherits": "fdm_bbl_3dp_001_common",
        "instantiation": "true",
        "printer_model": "Bambu Lab A1 mini",
        "nozzle_diameter": ["0.4"],
    },
    "Bambu Lab X1 Carbon 0.4 nozzle": {
        "name": "Bambu Lab X1 Carbon 0.4 nozzle",
        "inherits": "fdm_bbl_3dp_001_common",
        "instantiation": "true",
        "printer_model": "Bambu Lab X1 Carbon",
        "nozzle_diameter": ["0.4"],
    },
}

PROCESSES = {
    "0.20mm Standard @BBL A1M": {
        "name": "0.20mm Standard @BBL A1M",
        "instantiation": "true",
        "compatible_printers": ["Bambu Lab A1 mini 0.4 nozzle"],
        "layer_height": "0.2",
    },
    "0.20mm Standard @BBL X1C": {
        "name": "0.20mm Standard @BBL X1C",
        "instantiation": "true",
        "compatible_printers": ["Bambu Lab X1 Carbon 0.4 nozzle"],
        "layer_height": "0.2",
    },
}

FILAMENTS = {
    # Abstract grandparent -> abstract parent -> instantiated leaf (chain
    # three levels deep).
    "fdm_filament_bbl_common": {
        "name": "fdm_filament_bbl_common",
        "instantiation": "false",
        "filament_type": "PLA",
    },
    "fdm_filament_pla": {
        "name": "fdm_filament_pla",
        "inherits": "fdm_filament_bbl_common",
        "instantiation": "false",
        "filament_density": "1.24",
    },
    "Bambu PLA Basic @BBL A1M": {
        "name": "Bambu PLA Basic @BBL A1M",
        "inherits": "fdm_filament_pla",
        "instantiation": "true",
        "compatible_printers": ["Bambu Lab A1 mini 0.4 nozzle"],
        "filament_colour": "#FFFFFF",
    },
    "Bambu PLA Basic @BBL X1C": {
        "name": "Bambu PLA Basic @BBL X1C",
        "inherits": "fdm_filament_pla",
        "instantiation": "true",
        "compatible_printers": ["Bambu Lab X1 Carbon 0.4 nozzle"],
        "filament_colour": "#FFFFFF",
    },
}


def write_bundle(root: Path) -> Path:
    """Writes the fixture tree under `root` (created if missing) and
    returns `root`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "BBL.json").write_text(json.dumps(BBL_JSON))
    for dirname, presets in (
        ("machine", MACHINES),
        ("process", PROCESSES),
        ("filament", FILAMENTS),
    ):
        d = root / dirname
        d.mkdir(exist_ok=True)
        for name, data in presets.items():
            (d / f"{name}.json").write_text(json.dumps(data))
    return root


def write_bundle_zip(zip_path: Path, tmp_tree: Path) -> Path:
    """Builds the fixture tree under `tmp_tree` then zips it (with the BBL
    files at the zip root, not nested under a folder) to `zip_path`."""
    write_bundle(tmp_tree)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for file in tmp_tree.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(tmp_tree))
    return zip_path
