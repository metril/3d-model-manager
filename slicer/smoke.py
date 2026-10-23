"""Smoke test against a running sidecar: slices a 20 mm cube with the
bundled A1 mini / 0.20mm Standard / Bambu PLA Basic profiles and checks the
result is a .gcode.3mf. Usage: python smoke.py [base_url]. Stdlib only."""

import io
import json
import struct
import sys
import urllib.request
import uuid
import zipfile

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3001").rstrip("/")


def cube_stl() -> bytes:
    v = [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0), (0, 0, 20), (20, 0, 20), (20, 20, 20), (0, 20, 20)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    out = bytearray(80) + struct.pack("<I", len(f))
    for a, b, c in f:
        out += struct.pack("<12fH", 0, 0, 0, *v[a], *v[b], *v[c], 0)
    return bytes(out)


def multipart(files, fields):
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, (filename, data) in files.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                 f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        body += data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


health = get("/health")
assert health["status"] == "ok", health
by = {(p["kind"], p["name"]): p["data"] for p in get("/profiles?flatten=1")}
printer = by[("printer", "Bambu Lab A1 mini 0.4 nozzle")]
process = by[("process", "0.20mm Standard @BBL A1M")]
filament = by[("filament", "Bambu PLA Basic @BBL A1M")]
body, ctype = multipart(
    {
        "model": ("cube.stl", cube_stl()),
        "printer_profile": ("printer_profile.json", json.dumps(printer).encode()),
        "process_profile": ("process_profile.json", json.dumps(process).encode()),
        "filament_profiles": ("filament_profiles.json", json.dumps([filament]).encode()),
    },
    {"bed_type": "textured_plate", "options": json.dumps({"auto_orient": True, "arrange": True, "plate": None})},
)
req = urllib.request.Request(BASE + "/slice", data=body, headers={"Content-Type": ctype})
with urllib.request.urlopen(req, timeout=600) as r:
    out = r.read()
names = zipfile.ZipFile(io.BytesIO(out)).namelist()
assert "Metadata/plate_1.gcode" in names, names
print(f"smoke ok: bambu {health['version']}, {len(out)} bytes, plate_1.gcode present")
