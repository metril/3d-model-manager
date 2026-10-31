import json
import stat
import sys
from pathlib import Path

import pytest

# Fake CLI: behaviour picked by the model file's content.
FAKE_CLI = """#!/usr/bin/env python3
import json, sys, time, pathlib
args = sys.argv[1:]
out = pathlib.Path(args[args.index("--outputdir") + 1])
model = pathlib.Path(args[-1])
mode = model.read_text() if model.suffix == ".stl" else "ok"
(out.parent / "argv.json").write_text(json.dumps(args))
if mode == "hang":
    (out.parent / "pid").write_text("x")
    time.sleep(60)
elif mode == "fail":
    print("boom on stderr", file=sys.stderr); sys.exit(3)
elif mode == "silent":
    sys.exit(0)
else:
    import zipfile
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out / args[args.index("--export-3mf") + 1], "w") as z:
        z.writestr("Metadata/plate_1.gcode", "G1")
    (out / "result.json").write_text(json.dumps({"return_code": 0}))
"""


@pytest.fixture
def fake_cli(tmp_path) -> Path:
    path = tmp_path / "fake-slicer"
    path.write_text(FAKE_CLI.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def bbl(tmp_path) -> Path:
    root = tmp_path / "BBL"
    for d in ("machine", "process", "filament"):
        (root / d).mkdir(parents=True)

    def put(d, name, **kw):
        (root / d / f"{name}.json").write_text(json.dumps({"name": name, **kw}))

    put("machine", "base", instantiation="false", nozzle_diameter=["0.4"])
    put("machine", "A1M 0.4", inherits="base", instantiation="true", printer_model="A1M")
    put("process", "pbase", instantiation="false", layer_height="0.2")
    put(
        "process",
        "0.20 std",
        inherits="pbase",
        instantiation="true",
        compatible_printers=["A1M 0.4"],
    )
    put("filament", "fbase", instantiation="false", filament_type=["PLA"])
    put("filament", "PLA mid", inherits="fbase", instantiation="false")
    put("filament", "PLA basic", inherits="PLA mid", instantiation="true")
    put("filament", "cyc-a", inherits="cyc-b", instantiation="true")
    put("filament", "cyc-b", inherits="cyc-a", instantiation="true")
    return root
