"""M2 processing-pipeline end-to-end flow (Task 10): drives a REAL, running
docker compose stack over plain HTTP through metadata extraction, GLB
conversion, meshopt optimization, and thumbnail rendering for a plain mesh
(STL), plus the sliced-``.gcode.3mf`` plate/print-time path and the gallery's
``has_sliced`` filter.

See ``test_m1_flow.py``'s module docstring for the shared e2e conventions:
dependency-light, no imports from ``app.*``, run via ``scripts/e2e.sh``
against the built Docker image over the network rather than the source tree
in-process.

The STL and ``.gcode.3mf`` fixtures below are inlined, trimmed-down copies of
``backend/tests/corpus.py``'s ``box_stl()``/``sliced_gcode_3mf()`` builders
(same ground truths: a 12-triangle box; a 2-plate sliced 3mf with
``print_time_s`` 3600 + 1800 = 5400) -- duplicated rather than imported so
this file stays free of the backend source tree.
"""

from __future__ import annotations

import os
import struct
import time
import zipfile
from io import BytesIO

import httpx
import pytest
from PIL import Image

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")

PIPELINE_POLL_TIMEOUT_S = 120.0
POLL_INTERVAL_S = 0.5

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GLB_MAGIC = b"glTF"
IMMUTABLE_CACHE_CONTROL = "private, max-age=31536000, immutable"

# -- tiny binary STL: a 20x10x5 mm box, 8 vertices / 12 triangles ------------
# Same box as backend/tests/corpus.py's box_stl()/_CUBE_VERTICES (matching
# trimesh.creation.box(extents=(20, 10, 5))): watertight, ground-truth
# triangle_count 12.

_STL_HEADER = b"tdmm-e2e-m2-box".ljust(80, b"\0")

_BOX_VERTICES = [
    (-10.0, -5.0, -2.5),
    (10.0, -5.0, -2.5),
    (10.0, 5.0, -2.5),
    (-10.0, 5.0, -2.5),
    (-10.0, -5.0, 2.5),
    (10.0, -5.0, 2.5),
    (10.0, 5.0, 2.5),
    (-10.0, 5.0, 2.5),
]

_BOX_TRIANGLES = [
    (0, 3, 2),
    (0, 2, 1),
    (4, 5, 6),
    (4, 6, 7),
    (0, 1, 5),
    (0, 5, 4),
    (3, 7, 6),
    (3, 6, 2),
    (0, 4, 7),
    (0, 7, 3),
    (1, 6, 5),
    (1, 2, 6),
]


def _pack_triangle(
    v1: tuple[float, float, float], v2: tuple[float, float, float], v3: tuple[float, float, float]
) -> bytes:
    # Normal left as all-zero: nothing downstream (trimesh) requires a
    # correct precomputed normal to load the mesh or count its triangles.
    return struct.pack("<12fH", 0.0, 0.0, 0.0, *v1, *v2, *v3, 0)


def generate_box_stl() -> bytes:
    """A deterministic, watertight, 12-triangle binary STL box (no trimesh
    dependency -- see module docstring)."""
    body = bytearray()
    for a, b, c in _BOX_TRIANGLES:
        body += _pack_triangle(_BOX_VERTICES[a], _BOX_VERTICES[b], _BOX_VERTICES[c])
    return _STL_HEADER + struct.pack("<I", len(_BOX_TRIANGLES)) + bytes(body)


# -- hand-built sliced .gcode.3mf: 2 plates, print_time_s 5400 ---------------
# Trimmed-down inline copy of backend/tests/corpus.py's sliced_gcode_3mf():
# same shape (Metadata/slice_info.config + project_settings.config +
# model_settings.config, one plate's gcode, both plates' thumbnail PNGs, a
# geometry-free stub 3D/3dmodel.model), same ground truths (plate_count 2,
# print_time_s 3600 + 1800 = 5400).

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" '
    'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    "</Types>"
)
_ROOT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" '
    'Target="/3D/3dmodel.model"/>'
    "</Relationships>"
)
_STUB_MODEL_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<model unit="millimeter" xml:lang="en-US" '
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
    "<resources/>"
    "<build/>"
    "</model>"
)
_SLICE_INFO_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<config>"
    "<plate>"
    '<metadata key="index" value="1"/>'
    '<metadata key="prediction" value="3600"/>'
    '<metadata key="weight" value="12.50"/>'
    '<filament id="1" type="PLA" color="#FF0000" used_m="4.82" used_g="12.50"/>'
    "</plate>"
    "<plate>"
    '<metadata key="index" value="2"/>'
    '<metadata key="prediction" value="1800"/>'
    '<metadata key="weight" value="7.50"/>'
    '<filament id="1" type="PETG" color="#0000FF" used_m="2.41" used_g="7.50"/>'
    "</plate>"
    "</config>"
)
_PROJECT_SETTINGS_JSON = (
    '{"printer_model": "Bambu Lab A1 mini", "printer_variant": "0.4", '
    '"layer_height": "0.2", "filament_type": ["PLA", "PETG"]}'
)
_MODEL_SETTINGS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<config>"
    "<plate>"
    '<metadata key="plater_id" value="1"/>'
    '<metadata key="gcode_file" value="Metadata/plate_1.gcode"/>'
    '<metadata key="thumbnail_file" value="Metadata/plate_1.png"/>'
    "</plate>"
    "<plate>"
    '<metadata key="plater_id" value="2"/>'
    '<metadata key="gcode_file" value="Metadata/plate_2.gcode"/>'
    '<metadata key="thumbnail_file" value="Metadata/plate_2.png"/>'
    "</plate>"
    "</config>"
)
_BAMBU_GCODE = (
    b"; HEADER_BLOCK_START\n"
    b"; BambuStudio 02.00.00.00\n"
    b"; model printing time: 55m 30s; total estimated time: 1h 0m 0s\n"
    b"; total layer number: 175\n"
    b"; total filament length [mm] : 4820.5\n"
    b"; total filament weight [g] : 12.50\n"
    b"; max_z_height: 35.00\n"
    b"; HEADER_BLOCK_END\n"
    b"G28\n"
)


def _solid_png(size: int, color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (size, size), color)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def generate_sliced_gcode_3mf() -> bytes:
    """A hand-built 2-plate sliced ``.gcode.3mf`` (only plate 1's gcode is
    included, matching ``corpus.sliced_gcode_3mf``'s deliberate omission of
    plate 2's -- both plates' thumbnails are still present)."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _ROOT_RELS_XML)
        zf.writestr("3D/3dmodel.model", _STUB_MODEL_XML)
        zf.writestr("Metadata/slice_info.config", _SLICE_INFO_XML)
        zf.writestr("Metadata/project_settings.config", _PROJECT_SETTINGS_JSON)
        zf.writestr("Metadata/model_settings.config", _MODEL_SETTINGS_XML)
        zf.writestr("Metadata/plate_1.png", _solid_png(32, (255, 0, 0)))
        zf.writestr("Metadata/plate_2.png", _solid_png(32, (0, 0, 255)))
        zf.writestr("Metadata/plate_1.gcode", _BAMBU_GCODE)
    return buf.getvalue()


def _admin_password() -> str:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        pytest.fail(
            "ADMIN_PASSWORD must be set for the e2e run (scripts/e2e.sh sets a "
            "fixed one in .env so this test can log in with a known password)"
        )
    return password


def _login(client: httpx.Client) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": _admin_password()},
    )
    assert response.status_code == 204, response.text


def _upload(
    client: httpx.Client, *, model_id: int, revision_id: int, rel_path: str, content: bytes
) -> dict:
    response = client.put(
        "/api/uploads",
        params={"model_id": model_id, "revision_id": revision_id, "rel_path": rel_path},
        content=content,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _poll_pipeline_steps_done(
    client: httpx.Client, *, upload_job_id: str, file_id: int, steps: set[str]
) -> None:
    """Poll ``GET /api/jobs`` until every one of ``steps`` has a ``done`` job
    for ``file_id`` (``subject_id``). Fails fast (with the job's error) if
    the triggering upload job or any matching pipeline-step job lands in
    ``failed`` instead.
    """
    deadline = time.monotonic() + PIPELINE_POLL_TIMEOUT_S
    done: set[str] = set()
    last_jobs: list[dict] = []
    while time.monotonic() < deadline:
        response = client.get("/api/jobs", params={"limit": 200})
        assert response.status_code == 200, response.text
        jobs = response.json()
        last_jobs = jobs

        upload_job = next((j for j in jobs if j["id"] == upload_job_id), None)
        if upload_job is not None and upload_job["state"] == "failed":
            pytest.fail(f"store_to_backend job {upload_job_id} failed: {upload_job['error']}")

        for job in jobs:
            if job["subject_id"] != file_id or job["type"] not in steps:
                continue
            if job["state"] == "failed":
                pytest.fail(f"pipeline job {job['id']} ({job['type']}) failed: {job['error']}")
            if job["state"] == "done":
                done.add(job["type"])

        if done == steps:
            return
        time.sleep(POLL_INTERVAL_S)

    pytest.fail(
        f"pipeline steps {steps - done} for file {file_id} did not complete within "
        f"{PIPELINE_POLL_TIMEOUT_S}s; last jobs seen: {last_jobs}"
    )


def _poll_for_200(client: httpx.Client, url: str) -> httpx.Response:
    deadline = time.monotonic() + PIPELINE_POLL_TIMEOUT_S
    last_response: httpx.Response | None = None
    while time.monotonic() < deadline:
        response = client.get(url)
        if response.status_code == 200:
            return response
        last_response = response
        time.sleep(POLL_INTERVAL_S)
    detail = last_response.text if last_response is not None else "<no response>"
    status = last_response.status_code if last_response is not None else None
    pytest.fail(f"{url} did not return 200 within {PIPELINE_POLL_TIMEOUT_S}s: {status} {detail}")


def test_m2_pipeline_flow() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        _login(client)

        # -- create model + upload a tiny STL box ----------------------------
        create_response = client.post("/api/models", json={"name": "E2E M2 Pipeline Model"})
        assert create_response.status_code == 201, create_response.text
        model = create_response.json()
        slug = model["slug"]
        revision = model["current_revision"]

        stl_bytes = generate_box_stl()
        stl_upload = _upload(
            client,
            model_id=model["id"],
            revision_id=revision["id"],
            rel_path="box.stl",
            content=stl_bytes,
        )
        stl_hash = stl_upload["blob_hash"]

        _poll_pipeline_steps_done(
            client,
            upload_job_id=stl_upload["job_id"],
            file_id=stl_upload["file_id"],
            steps={"extract_metadata", "convert_to_glb", "optimize_glb", "render_thumb"},
        )

        # -- blob derivatives: thumb (immutable-cached PNG) + glb -------------
        thumb_response = client.get(f"/api/blobs/{stl_hash}/thumb")
        assert thumb_response.status_code == 200, thumb_response.text
        assert thumb_response.content[:8] == PNG_MAGIC
        assert thumb_response.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL

        glb_response = client.get(f"/api/blobs/{stl_hash}/glb")
        assert glb_response.status_code == 200, glb_response.text
        assert glb_response.content[:4] == GLB_MAGIC

        # -- model detail: mesh metadata + thumb/glb readiness -----------------
        detail = client.get(f"/api/models/{slug}").json()
        stl_file = next(
            f for f in detail["current_revision"]["files"] if f["rel_path"] == "box.stl"
        )
        assert stl_file["meta"]["triangle_count"] == 12
        assert stl_file["thumb_ready"] is True
        assert stl_file["glb_status"] == "ok"

        # -- gallery: a ready cover image ---------------------------------------
        gallery = client.get("/api/models", params={"q": "E2E M2 Pipeline Model"}).json()
        summary = next(item for item in gallery["items"] if item["slug"] == slug)
        assert summary["cover"] is not None

        # -- upload a hand-built sliced .gcode.3mf into the same revision -------
        gcode_3mf_bytes = generate_sliced_gcode_3mf()
        gcode_upload = _upload(
            client,
            model_id=model["id"],
            revision_id=revision["id"],
            rel_path="print.gcode.3mf",
            content=gcode_3mf_bytes,
        )
        gcode_hash = gcode_upload["blob_hash"]

        _poll_pipeline_steps_done(
            client,
            upload_job_id=gcode_upload["job_id"],
            file_id=gcode_upload["file_id"],
            steps={"extract_metadata", "extract_embedded_thumbs"},
        )

        # -- both plate thumbnails served ----------------------------------------
        for index in (1, 2):
            plate_response = client.get(f"/api/blobs/{gcode_hash}/plates/{index}/thumb")
            assert plate_response.status_code == 200, plate_response.text
            assert plate_response.content[:8] == PNG_MAGIC

        # -- model detail: sliced metadata (print time / plate count) -----------
        detail_2 = client.get(f"/api/models/{slug}").json()
        gcode_file = next(
            f for f in detail_2["current_revision"]["files"] if f["rel_path"] == "print.gcode.3mf"
        )
        assert gcode_file["meta"]["print_time_s"] == 5400
        assert gcode_file["meta"]["plate_count"] == 2

        # -- gallery: has_sliced filter matches exactly this model ---------------
        sliced_gallery = client.get("/api/models", params={"has_sliced": "true"}).json()
        assert [item["slug"] for item in sliced_gallery["items"]] == [slug]
        assert sliced_gallery["items"][0]["print_time_s"] == 5400

        # -- assembly thumbnail: ready once the STL's glb conversion settles -----
        assembly_response = _poll_for_200(client, f"/api/revisions/{revision['id']}/assembly-thumb")
        assert assembly_response.content[:8] == PNG_MAGIC
