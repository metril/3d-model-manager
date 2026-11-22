"""M3 scan end-to-end flow (Task 9): drives the SPEC's own drill
(design.md:197 "upload → revision → move folder on share → rescan relinks
→ download verifies hash") against a REAL, running docker compose stack
over plain HTTP.

See `test_m1_flow.py`'s module docstring for the shared e2e conventions:
dependency-light, no imports from `app.*`, run via `scripts/e2e.sh` against
the built Docker image over the network rather than the source tree
in-process.

**Why `docker compose exec` for the filesystem moves**: the e2e host has
`./library` bind-mounted, but by default the api/worker containers write
into it as root (see PUID/PGID in the README) -- the pytest process
running this file, as a normal host user, generally cannot itself rename or
create directories there. Running `mv`/`cat >` *inside* the `api` container
sidesteps that entirely, and mirrors `test_m1_flow.py`'s own precedent of
shelling out to `docker compose` (there: `restart api`) as an accepted e2e
idiom for driving container-side effects the HTTP API doesn't expose.
"""

from __future__ import annotations

import os
import struct
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
from blake3 import blake3

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROOT = Path(os.environ.get("E2E_LIBRARY_ROOT", str(REPO_ROOT / "library")))
BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")

PIPELINE_POLL_TIMEOUT_S = 120.0
SCAN_POLL_TIMEOUT_S = 90.0
POLL_INTERVAL_S = 0.5

_TERMINAL_SCAN_STATES = {"done", "failed", "skipped"}

# -- tiny deterministic binary STL generator (inline copy of
# test_m1_flow.py's generate_binary_stl -- see that module's docstring for
# why this is duplicated rather than imported). `salt` shifts the grid so
# two calls with different salts produce byte-distinct content/hashes.

STL_HEADER = b"tdmm-e2e-m3-scan-generated-stl".ljust(80, b"\0")

_CUBE_FACES = [
    ((0, 0, -1), (0, 1, 2)),
    ((0, 0, -1), (0, 2, 3)),
    ((0, 0, 1), (4, 6, 5)),
    ((0, 0, 1), (4, 7, 6)),
    ((0, -1, 0), (0, 5, 1)),
    ((0, -1, 0), (0, 4, 5)),
    ((0, 1, 0), (3, 2, 6)),
    ((0, 1, 0), (3, 6, 7)),
    ((-1, 0, 0), (0, 3, 7)),
    ((-1, 0, 0), (0, 7, 4)),
    ((1, 0, 0), (1, 6, 2)),
    ((1, 0, 0), (1, 5, 6)),
]
_CUBE_OFFSETS = [
    (0, 0, 0),
    (1, 0, 0),
    (1, 1, 0),
    (0, 1, 0),
    (0, 0, 1),
    (1, 0, 1),
    (1, 1, 1),
    (0, 1, 1),
]


def _pack_triangle(
    normal: tuple[float, float, float],
    v1: tuple[float, float, float],
    v2: tuple[float, float, float],
    v3: tuple[float, float, float],
) -> bytes:
    return struct.pack("<12fH", *normal, *v1, *v2, *v3, 0)


def generate_binary_stl(min_size_bytes: int = 20_000, *, salt: int = 0) -> bytes:
    """A deterministic, procedurally-generated binary STL of a grid of unit
    cubes, sized to at least `min_size_bytes`. `salt` shifts the grid so a
    different salt yields different bytes/hash.
    """
    triangle_size = 50  # bytes: 12 f32 (normal + 3 vertices) + uint16 attr
    triangles_needed = max(12, -(-(min_size_bytes - 84) // triangle_size))
    cubes_needed = -(-triangles_needed // 12)

    body = bytearray()
    count = 0
    for i in range(cubes_needed):
        ox = float(i + salt * cubes_needed)
        base = [(ox + dx, float(dy), float(dz)) for dx, dy, dz in _CUBE_OFFSETS]
        for normal, (a, b, c) in _CUBE_FACES:
            body += _pack_triangle(normal, base[a], base[b], base[c])
            count += 1

    return STL_HEADER + struct.pack("<I", count) + bytes(body)


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
    """Poll `GET /api/jobs` until every one of `steps` has a `done` job for
    `file_id` (`subject_id`) -- fails fast (with the job's error) if the
    triggering upload job or any matching pipeline-step job lands in
    `failed`. Copied from test_m2_pipeline.py's helper of the same name;
    used here purely to reach a quiescent point (no in-flight reads of the
    file) before this test moves it around on disk.
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


def _trigger_scan(client: httpx.Client) -> dict:
    response = client.post("/api/scan")
    assert response.status_code == 201, response.text
    return response.json()


def _poll_scan_run(client: httpx.Client, scan_run_id: int) -> dict:
    deadline = time.monotonic() + SCAN_POLL_TIMEOUT_S
    last: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/scan-runs/{scan_run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["state"] in _TERMINAL_SCAN_STATES:
            if last["state"] != "done":
                pytest.fail(f"scan run {scan_run_id} ended in state {last['state']!r}: {last}")
            return last
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"scan run {scan_run_id} did not reach a terminal state within "
        f"{SCAN_POLL_TIMEOUT_S}s; last seen: {last}"
    )


# Containers run as non-root (PUID/PGID); exec as the same user so files
# created here are writable by the app.
_EXEC_USER = f"{os.environ.get('PUID', '1000')}:{os.environ.get('PGID', '1000')}"


def _compose_exec(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "--user", _EXEC_USER, "api", *args],
        check=True,
        cwd=REPO_ROOT,
        timeout=30,
    )


def _compose_write_file(container_path: str, content: bytes) -> None:
    """Create `container_path` (and its parent dir) inside the `api`
    container with `content`, piped over stdin -- the host-side equivalent
    of an out-of-band NAS drop straight onto the share.
    """
    parent = container_path.rsplit("/", 1)[0]
    shell_cmd = f"mkdir -p '{parent}' && cat > '{container_path}'"
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "--user",
            _EXEC_USER,
            "api",
            "sh",
            "-c",
            shell_cmd,
        ],
        input=content,
        check=True,
        cwd=REPO_ROOT,
        timeout=30,
    )


def test_m3_scan_relink_and_adopt() -> None:
    run_token = uuid.uuid4().hex[:8]

    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        _login(client)

        # -- create model + upload an STL, quiesce the pipeline -------------
        create_response = client.post("/api/models", json={"name": f"E2E Scan Model {run_token}"})
        assert create_response.status_code == 201, create_response.text
        model = create_response.json()
        model_id = model["id"]
        slug = model["slug"]
        revision = model["current_revision"]

        stl_bytes = generate_binary_stl()
        upload = _upload(
            client,
            model_id=model_id,
            revision_id=revision["id"],
            rel_path="box.stl",
            content=stl_bytes,
        )
        file_id = upload["file_id"]
        blob_hash = upload["blob_hash"]

        _poll_pipeline_steps_done(
            client,
            upload_job_id=upload["job_id"],
            file_id=file_id,
            steps={"extract_metadata", "convert_to_glb", "optimize_glb", "render_thumb"},
        )

        on_disk = LIBRARY_ROOT / slug / revision["dir_name"] / "box.stl"
        assert on_disk.is_file(), f"expected uploaded file at {on_disk}"
        assert blake3(on_disk.read_bytes()).hexdigest() == blob_hash

        # -- simulate an out-of-band NAS reorganization: move the whole ------
        # -- model folder to a new top-level name on the bind-mounted share --
        moved_slug = f"{slug}-relocated-{run_token}"
        _compose_exec("mv", f"/library/{slug}", f"/library/{moved_slug}")

        moved_on_disk = LIBRARY_ROOT / moved_slug / revision["dir_name"] / "box.stl"
        assert moved_on_disk.is_file(), f"expected relocated file at {moved_on_disk}"
        assert not on_disk.exists(), f"expected {on_disk} to be gone after the move"

        # -- rescan: the moved file relinks by hash --------------------------
        scan_1 = _poll_scan_run(client, _trigger_scan(client)["id"])
        assert scan_1["relinked"] >= 1, scan_1
        relinked_file_ids = {entry["file_id"] for entry in scan_1["report"]["relinked"]}
        assert file_id in relinked_file_ids, scan_1["report"]["relinked"]

        # -- still downloadable, and the bytes still verify against the blob --
        download_response = client.get(f"/api/files/{file_id}/download")
        assert download_response.status_code == 200, download_response.text
        assert blake3(download_response.content).hexdigest() == blob_hash

        # -- drop a brand-new, untracked folder straight onto the share ------
        adopt_dir_name = f"e2e-scan-adopted-{run_token}"
        adopt_filename = "widget.stl"
        adopt_bytes = generate_binary_stl(salt=1)
        assert adopt_bytes != stl_bytes
        _compose_write_file(f"/library/{adopt_dir_name}/{adopt_filename}", adopt_bytes)

        # -- rescan: the untracked folder is adopted as a new draft model ----
        scan_2 = _poll_scan_run(client, _trigger_scan(client)["id"])
        assert scan_2["adopted"] >= 1, scan_2
        adopted_entry = next(
            (entry for entry in scan_2["report"]["adopted"] if adopt_filename in entry["files"]),
            None,
        )
        assert adopted_entry is not None, scan_2["report"]["adopted"]
        adopted_slug = adopted_entry["slug"]

        # -- the adopted model shows up in the gallery -----------------------
        gallery = client.get("/api/models", params={"q": adopt_dir_name}).json()
        assert adopted_slug in {item["slug"] for item in gallery["items"]}, gallery
