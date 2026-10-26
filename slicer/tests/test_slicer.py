import asyncio
import io
import types
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, runner
from app.profiles import ProfileError, ProfileIndex

CLIENT_SRC = Path(__file__).resolve().parents[2] / "backend/app/services/slicer_client.py"


def _client_module():
    """Load the backend's real slicer_client (stubbing its app-config import)
    so the multipart body it builds is what the server is tested against."""
    src = CLIENT_SRC.read_text().replace(
        "from app.services.app_config import AppConfig", "AppConfig = object"
    )
    mod = types.ModuleType("slicer_client_under_test")
    import sys
    sys.modules[mod.__name__] = mod
    exec(compile(src, str(CLIENT_SRC), "exec"), mod.__dict__)
    return mod


# --- profiles --------------------------------------------------------------


def test_flatten_resolves_chain(bbl):
    ix = ProfileIndex(bbl)
    flat = ix.flatten("filament", "PLA basic")
    assert flat["filament_type"] == ["PLA"] and "inherits" not in flat


def test_flatten_cycle_and_missing_parent(bbl):
    ix = ProfileIndex(bbl)
    with pytest.raises(ProfileError, match="cycle"):
        ix.flatten("filament", "cyc-a")
    with pytest.raises(ProfileError, match="unknown parent"):
        ix.flatten_dict("process", {"name": "x", "inherits": "nope"})


def test_flatten_depth_guard():
    ix = ProfileIndex()
    for i in range(40):
        ix.by_kind["process"][f"p{i}"] = {"name": f"p{i}", "inherits": f"p{i + 1}"}
    with pytest.raises(ProfileError, match="too deep"):
        ix.flatten("process", "p0")


def test_entries_only_instantiable_with_model(bbl):
    ix = ProfileIndex(bbl)
    assert [e["name"] for e in ix.entries("printer")] == ["A1M 0.4"]
    proc = ix.entries("process", flatten=True)
    assert [e["name"] for e in proc] == ["0.20 std"]
    assert proc[0]["printer_model"] == "A1M" and proc[0]["data"]["layer_height"] == "0.2"
    names = [e["name"] for e in ix.entries("filament")]
    assert "PLA mid" not in names and "cyc-a" not in names  # cyc-* unresolvable


# --- runner ----------------------------------------------------------------


def test_build_argv_is_list_with_expected_flags(tmp_path):
    argv = runner.build_argv(
        cli="/x/bs",
        work=tmp_path,
        model=tmp_path / "m.stl",
        settings=[tmp_path / "p.json", tmp_path / "q.json"],
        filaments=[tmp_path / "f.json"],
        bed_type="textured_plate",
        auto_orient=True,
        arrange=False,
        plate=2,
    )
    assert argv[0] == "/x/bs"
    assert argv[argv.index("--curr-bed-type") + 1] == "Textured PEI Plate"
    assert argv[argv.index("--orient") + 1] == "1"
    assert argv[argv.index("--arrange") + 1] == "0"
    assert argv[argv.index("--slice") + 1] == "2"
    assert argv[argv.index("--load-settings") + 1].count(";") == 1
    assert "--export-3mf" in argv and argv[-1].endswith("m.stl")
    assert runner.build_argv(
        cli="c", work=tmp_path, model=tmp_path / "m", settings=[], filaments=[],
        bed_type="Cool Plate", auto_orient=False, arrange=False, plate=None, use_xvfb=True,
    )[:2] == ["xvfb-run", "-a"]
    with pytest.raises(runner.BadRequest):
        runner.bed_type_name("lava")


def _job(tmp_path, content="ok"):
    d = runner.new_job_dir()
    model = d / "model.stl"
    model.write_text(content)
    return runner.Job(d, model, [], [])


def _run(job, cli, timeout=10):
    return asyncio.run(
        runner.run_slice(
            cli=str(cli), job=job, bed_type="textured_plate", auto_orient=False,
            arrange=False, plate=None, timeout_s=timeout,
        )
    )


def test_run_slice_ok(fake_cli):
    job = _job(None)
    try:
        assert zipfile.is_zipfile(io.BytesIO(_run(job, fake_cli)))
    finally:
        runner.cleanup(job.dir)
    assert not job.dir.exists()


def test_run_slice_nonzero_exit_has_log_tail(fake_cli):
    job = _job(None, "fail")
    try:
        with pytest.raises(runner.SliceFailed) as ei:
            _run(job, fake_cli)
    finally:
        runner.cleanup(job.dir)
    assert ei.value.exit_code == 3 and "boom" in ei.value.log_tail


def test_run_slice_exit0_without_output_is_failure(fake_cli):
    job = _job(None, "silent")
    try:
        with pytest.raises(runner.SliceFailed, match="no output"):
            _run(job, fake_cli)
    finally:
        runner.cleanup(job.dir)


def test_run_slice_timeout_kills(fake_cli):
    job = _job(None, "hang")
    try:
        with pytest.raises(runner.SliceTimeout):
            _run(job, fake_cli, timeout=1)
    finally:
        runner.cleanup(job.dir)
    assert not job.dir.exists()


def test_run_slice_cancel_kills(fake_cli):
    job = _job(None, "hang")
    handle = runner.JobHandle()

    async def go():
        task = asyncio.create_task(
            runner.run_slice(
                cli=str(fake_cli), job=job, bed_type="textured_plate", auto_orient=False,
                arrange=False, plate=None, timeout_s=30, handle=handle,
            )
        )
        while handle.proc is None:
            await asyncio.sleep(0.05)
        handle.cancel()
        await task

    try:
        with pytest.raises(runner.SliceCancelled):
            asyncio.run(go())
    finally:
        runner.cleanup(job.dir)


# --- HTTP ------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch, fake_cli, bbl):
    monkeypatch.setattr(main, "CLI", str(fake_cli))
    monkeypatch.setattr(main.app.state, "index", ProfileIndex(bbl))
    monkeypatch.setattr(main, "JOB_TIMEOUT_S", 3)
    return TestClient(main.app)


def _body(model=b"ok", **over):
    mod = _client_module()
    req = mod._build_slice_request(
        model=model,
        filename="cube.stl",
        printer_profile={"name": "A1M 0.4", "inherits": "base", "printer_model": "A1M"},
        process_profile={"name": "mine", "inherits": "pbase"},
        filament_profiles=[{"name": "PLA basic"}],
        bed_type="textured_plate",
        options=mod.SliceOptions(auto_orient=True, arrange=True, plate=None),
    )
    return req


def test_health_and_profiles(api):
    h = api.get("/health").json()
    assert h["status"] == "ok" and h["max_concurrency"] == 1 and h["busy"] is False
    assert {e["kind"] for e in api.get("/profiles").json()} == {"printer", "process", "filament"}
    assert "data" not in api.get("/profiles").json()[0]
    assert "data" in api.get("/profiles?flatten=1").json()[0]


def test_slice_roundtrip_with_client_contract(api):
    r = api.post("/slice", **_body())
    assert r.status_code == 200 and zipfile.is_zipfile(io.BytesIO(r.content))


def test_slice_bad_profile_400(api):
    req = _body()
    req["files"] = [
        (k, ("process_profile.json", b'{"name":"x","inherits":"zzz"}', "application/json"))
        if k == "process_profile" else (k, v)
        for k, v in req["files"]
    ]
    r = api.post("/slice", **req)
    assert r.status_code == 400 and r.json()["error"] == "bad_profile"


def test_slice_cli_failure_422(api):
    r = api.post("/slice", **_body(model=b"fail"))
    j = r.json()
    assert r.status_code == 422 and j["error"] == "slice_failed" and j["exit_code"] == 3
    assert "boom" in j["log_tail"]


def test_slice_timeout_504(api):
    r = api.post("/slice", **_body(model=b"hang"))
    assert r.status_code == 504 and r.json()["error"] == "timeout"


def test_slice_busy_429(api):
    async def hold():
        await main.app.state.sem.acquire()

    asyncio.run(hold())
    try:
        r = api.post("/slice", **_body())
        assert r.status_code == 429 and r.headers["retry-after"]
    finally:
        main.app.state.sem.release()


def test_slice_missing_parts_400(api):
    assert api.post("/slice", data={"bed_type": "x"}).status_code == 400
    assert api.post("/slice", data={}).json()["error"] == "bad_request"


def test_slice_oversize_content_length_413_before_parsing(api, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD", 10)
    r = api.post("/slice", content=b"x", headers={"content-length": str(5 * 1024 * 1024)})
    assert r.status_code == 413


def test_slice_bad_options_400(api):
    for bad in ("[1]", '"s"', "5", "{"):
        req = _body()
        req["data"]["options"] = bad
        assert api.post("/slice", **req).status_code == 400, bad
    req = _body()
    req["data"]["options"] = '{"plate": "abc"}'
    assert api.post("/slice", **req).status_code == 400
    req = _body()
    req["data"].pop("options")
    req["files"].append(("options", ("options", b"{}", "application/json")))
    assert api.post("/slice", **req).status_code == 400


def test_health_reports_limits(api):
    h = api.get("/health").json()
    assert h["job_timeout_s"] == 3 and h["running"] == 0
    assert "mem_limit" in h and "bambu_version" in h


def test_delete_unknown_job_404(api):
    assert api.delete("/jobs/nope").status_code == 404


def test_delete_job_cancels_running_slice(api, monkeypatch):
    import threading
    import time

    monkeypatch.setattr(main, "JOB_TIMEOUT_S", 30)
    req = _body(model=b"hang")
    req["headers"] = {"x-job-id": "j1"}
    out = {}
    t = threading.Thread(target=lambda: out.update(r=api.post("/slice", **req)))
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        h = main.app.state.jobs.get("j1")
        if h is not None and h.proc is not None:
            break
        time.sleep(0.05)
    assert api.delete("/jobs/j1").json()["status"] == "cancelled"
    t.join(10)
    assert out["r"].status_code == 499 and out["r"].json()["error"] == "cancelled"
    assert "j1" not in main.app.state.jobs


def test_slice_success_returns_log_header(api):
    r = api.post("/slice", **_body())
    assert r.status_code == 200 and "x-slice-log" in r.headers
    import base64

    base64.b64decode(r.headers["x-slice-log"], validate=True)
