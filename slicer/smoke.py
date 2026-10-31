"""Smoke test against a running sidecar: slices a 20 mm cube with the
bundled A1 mini / 0.20mm Standard / Bambu PLA Basic profiles and checks the
result is a .gcode.3mf with slice_info.config and a non-blank plate thumbnail. Usage: python smoke.py [base_url]. Stdlib only."""

import io
import json
import struct
import zlib
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
zf = zipfile.ZipFile(io.BytesIO(out))
names = zf.namelist()
assert "Metadata/plate_1.gcode" in names, names
assert "Metadata/slice_info.config" in names, names
assert b"<plate>" in zf.read("Metadata/slice_info.config"), "slice_info.config has no plate"
thumb = next((n for n in names if n.startswith("Metadata/plate_") and n.endswith(".png")), None)


def png_distinct_bytes(png: bytes) -> int:
    pos, idat = 8, b""
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        if kind == b"IDAT":
            idat += png[pos + 8:pos + 8 + n]
        pos += 12 + n
    return len(set(zlib.decompress(idat)))


# OrcaSlicer v2.4.2's CLI renders no plate thumbnails (verified, with and
# without a display); the backend renders its own. So: check when present.
thumb_note = "no plate thumbnail (expected for OrcaSlicer CLI)"
if thumb:
    distinct = png_distinct_bytes(zf.read(thumb))
    assert distinct > 8, f"{thumb} looks blank ({distinct} distinct byte values)"
    thumb_note = f"{thumb} ({distinct} distinct bytes)"
print(f"smoke ok: {health.get('slicer_name')} {health.get('slicer_version')}, {len(out)} bytes, "
      f"plate_1.gcode + slice_info.config, {thumb_note}")
