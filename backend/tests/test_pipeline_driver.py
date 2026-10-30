"""Pipeline driver tests (SPEC "Processing pipeline"; Global Constraints
"Pipeline shape" / "Pipeline jobs"): ``next_step``'s table-driven format
traversal, the shared ``run_step`` runner's mechanics (dispatch chaining,
failure isolation from the triggering upstream job, the completed hook), and
the generalized pipeline-step branch of ``services.jobs.retry_job``.

A test-only ``@pipeline_step("test_step")``/``"test_step_2"`` pair is
registered at IMPORT TIME (module scope), exactly like a real step task would
be by Tasks 3-6 -- Celery's eager mode (session-scoped autouse fixture, see
conftest.py) runs it inline, in-process, with no broker involved. Their
behavior is driven by module-level mutable state (``_step_state``/
``_step_2_state``) reset before/after every test in this module so tests
can't leak into each other.
"""

from __future__ import annotations

import httpx
import pytest

from app.models import File, Job
from app.models.enums import BlobFormat
from app.tasks import pipeline
from app.tasks.pipeline import PIPELINE_STEPS, next_step, pipeline_step, run_step
from tests.corpus import CorpusPaths

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


# ---------------------------------------------------------------------------
# next_step: pure, table-driven over the Global Constraints table.
# ---------------------------------------------------------------------------


def test_pipeline_steps_matches_global_constraints_table() -> None:
    assert PIPELINE_STEPS == {
        BlobFormat.STL: ("extract_metadata", "convert_to_glb", "optimize_glb", "render_thumb"),
        BlobFormat.OBJ: ("extract_metadata", "convert_to_glb", "optimize_glb", "render_thumb"),
        BlobFormat.THREEMF: (
            "extract_metadata",
            "extract_embedded_thumbs",
            "convert_to_glb",
            "optimize_glb",
            "render_thumb",
        ),
        BlobFormat.GCODE_3MF: ("extract_metadata", "extract_embedded_thumbs"),
        BlobFormat.GCODE: ("extract_metadata",),
        BlobFormat.STEP: ("convert_to_glb", "extract_metadata", "optimize_glb", "render_thumb"),
        BlobFormat.IGES: ("convert_to_glb", "extract_metadata", "optimize_glb", "render_thumb"),
        BlobFormat.PNG: ("render_thumb",),
        BlobFormat.JPG: ("render_thumb",),
        BlobFormat.WEBP: ("render_thumb",),
        # R13c: doc kinds never enter the GLB/thumb pipeline.
        BlobFormat.PDF: ("render_thumb",),
        BlobFormat.MD: (),
        BlobFormat.TXT: (),
        BlobFormat.DOCX: (),
        BlobFormat.OTHER: (),
    }


@pytest.mark.parametrize(
    "fmt, after, expected",
    [
        (BlobFormat.STL, None, "extract_metadata"),
        (BlobFormat.STL, "extract_metadata", "convert_to_glb"),
        (BlobFormat.STL, "convert_to_glb", "optimize_glb"),
        (BlobFormat.STL, "optimize_glb", "render_thumb"),
        (BlobFormat.STL, "render_thumb", None),
        (BlobFormat.STL, "unknown_step", None),
        (BlobFormat.OBJ, None, "extract_metadata"),
        (BlobFormat.OBJ, "render_thumb", None),
        (BlobFormat.THREEMF, None, "extract_metadata"),
        (BlobFormat.THREEMF, "extract_metadata", "extract_embedded_thumbs"),
        (BlobFormat.THREEMF, "extract_embedded_thumbs", "convert_to_glb"),
        (BlobFormat.THREEMF, "convert_to_glb", "optimize_glb"),
        (BlobFormat.THREEMF, "optimize_glb", "render_thumb"),
        (BlobFormat.THREEMF, "render_thumb", None),
        (BlobFormat.GCODE_3MF, None, "extract_metadata"),
        (BlobFormat.GCODE_3MF, "extract_metadata", "extract_embedded_thumbs"),
        (BlobFormat.GCODE_3MF, "extract_embedded_thumbs", None),
        (BlobFormat.GCODE, None, "extract_metadata"),
        (BlobFormat.GCODE, "extract_metadata", None),
        (BlobFormat.STEP, None, "convert_to_glb"),
        (BlobFormat.STEP, "convert_to_glb", "extract_metadata"),
        (BlobFormat.STEP, "extract_metadata", "optimize_glb"),
        (BlobFormat.STEP, "optimize_glb", "render_thumb"),
        (BlobFormat.STEP, "render_thumb", None),
        (BlobFormat.IGES, None, "convert_to_glb"),
        (BlobFormat.IGES, "render_thumb", None),
        (BlobFormat.PNG, None, "render_thumb"),
        (BlobFormat.PNG, "render_thumb", None),
        (BlobFormat.JPG, None, "render_thumb"),
        (BlobFormat.JPG, "render_thumb", None),
        (BlobFormat.WEBP, None, "render_thumb"),
        (BlobFormat.WEBP, "render_thumb", None),
        (BlobFormat.OTHER, None, None),
    ],
)
def test_next_step(fmt: BlobFormat, after: str | None, expected: str | None) -> None:
    assert next_step(fmt, after) == expected


# ---------------------------------------------------------------------------
# Driver mechanics: test-only steps, registered exactly like real ones would
# be by Tasks 3-6.
# ---------------------------------------------------------------------------

_step_state: dict[str, object] = {"outcome": "done", "calls": []}
_step_2_state: dict[str, object] = {"outcome": "done", "calls": []}


def _test_fn(session, settings, backend, blob):
    _step_state["calls"].append(blob.hash)
    outcome = _step_state["outcome"]
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


@pipeline_step("test_step")
def _test_step_task(job_id: str, blob_hash: str) -> None:
    run_step(job_id, blob_hash, "test_step", _test_fn)


def _test_fn_2(session, settings, backend, blob):
    _step_2_state["calls"].append(blob.hash)
    outcome = _step_2_state["outcome"]
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


@pipeline_step("test_step_2")
def _test_step_2_task(job_id: str, blob_hash: str) -> None:
    run_step(job_id, blob_hash, "test_step_2", _test_fn_2)


@pytest.fixture(autouse=True)
def _reset_test_step_state():
    def _reset() -> None:
        _step_state["outcome"] = "done"
        _step_state["calls"] = []
        _step_2_state["outcome"] = "done"
        _step_2_state["calls"] = []

    _reset()
    yield
    _reset()


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _upload_stl(
    client: httpx.AsyncClient,
    *,
    model_id: int,
    revision_id: int,
    content: bytes = b"some-real-pipeline-bytes",
) -> httpx.Response:
    return await client.put(
        "/api/uploads",
        params={"model_id": model_id, "revision_id": revision_id, "rel_path": "part.stl"},
        content=content,
    )


async def test_upload_dispatches_single_step_runs_it_and_calls_assembly_trigger(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 6 replaces the no-op ``pipeline_completed_hook`` with
    ``maybe_enqueue_assembly_sync`` as ``run_step``'s "last step" callback --
    same call site, same "called once the format's step chain is exhausted"
    contract, just a real name/signature (keyword-only ``blob_hash``) now.
    """
    monkeypatch.setattr(pipeline, "PIPELINE_STEPS", {BlobFormat.STL: ("test_step",)})
    hook_calls: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "maybe_enqueue_assembly_sync",
        lambda session, *, blob_hash: hook_calls.append(blob_hash),
    )

    created = await _create_model(authenticated_client, "Pipeline Driver Target")
    revision_id = created["current_revision"]["id"]
    upload = await _upload_stl(
        authenticated_client, model_id=created["id"], revision_id=revision_id
    )
    assert upload.status_code == 201, upload.text
    blob_hash = upload.json()["blob_hash"]

    jobs_resp = await authenticated_client.get("/api/jobs")
    step_jobs = [j for j in jobs_resp.json() if j["type"] == "test_step"]

    assert len(step_jobs) == 1
    assert step_jobs[0]["state"] == "done"
    assert step_jobs[0]["subject_type"] == "file"
    assert step_jobs[0]["subject_id"] == upload.json()["file_id"]
    assert _step_state["calls"] == [blob_hash]
    assert hook_calls == [blob_hash]


async def test_step_fn_raising_fails_its_own_job_but_upstream_store_job_stays_done(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "PIPELINE_STEPS", {BlobFormat.STL: ("test_step",)})
    _step_state["outcome"] = RuntimeError("simulated step failure")

    created = await _create_model(authenticated_client, "Step Failure Target")
    revision_id = created["current_revision"]["id"]
    upload = await _upload_stl(
        authenticated_client, model_id=created["id"], revision_id=revision_id
    )
    assert upload.status_code == 201, upload.text
    store_job_id = upload.json()["job_id"]

    jobs_resp = await authenticated_client.get("/api/jobs")
    jobs_list = jobs_resp.json()
    store_job = next(j for j in jobs_list if j["id"] == store_job_id)
    step_job = next(j for j in jobs_list if j["type"] == "test_step")

    assert store_job["state"] == "done"
    assert step_job["state"] == "failed"
    assert "simulated step failure" in step_job["error"]


async def test_skip_outcome_still_enqueues_next_step(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "PIPELINE_STEPS", {BlobFormat.STL: ("test_step", "test_step_2")})
    _step_state["outcome"] = "skipped"

    created = await _create_model(authenticated_client, "Skip Chain Target")
    revision_id = created["current_revision"]["id"]
    upload = await _upload_stl(
        authenticated_client, model_id=created["id"], revision_id=revision_id
    )
    assert upload.status_code == 201, upload.text
    blob_hash = upload.json()["blob_hash"]

    jobs_resp = await authenticated_client.get("/api/jobs")
    jobs_list = jobs_resp.json()
    first = next(j for j in jobs_list if j["type"] == "test_step")
    second = next(j for j in jobs_list if j["type"] == "test_step_2")

    assert first["state"] == "done"
    assert second["state"] == "done"
    assert _step_2_state["calls"] == [blob_hash]


async def test_full_stl_pipeline_runs_every_registered_step_to_done(
    authenticated_client: httpx.AsyncClient, corpus: CorpusPaths
) -> None:
    """PIPELINE_STEPS ships the real Global-Constraints table before every
    step has a registered body -- this test used to assert that uploading a
    real STL doesn't blow up dispatching a step name with no matching
    ``STEP_TASKS`` entry (Accept: "uploading any file still works end-to-end
    ... (empty or stubbed) pipeline dispatch"), checking that the
    STILL-unregistered next step had no job row at all. Task 6 registers
    ``render_thumb`` -- the last unregistered step for ``stl`` -- so there's
    no more stub step left to prove a no-op for; this now asserts the
    OPPOSITE: the full chain runs every ``PIPELINE_STEPS[stl]`` step to
    ``done``, and the job-row type set is exactly that chain plus the
    upload's own ingest job plus the ONE ``render_assembly_thumb`` job Task
    6's assembly trigger fires once ``render_thumb`` (the last step) finishes
    for this (single-file) revision -- not "no more unregistered steps show
    up", but "every step, including the new trigger, actually runs".
    """
    created = await _create_model(authenticated_client, "Full STL Pipeline Target")
    revision_id = created["current_revision"]["id"]

    upload = await _upload_stl(
        authenticated_client,
        model_id=created["id"],
        revision_id=revision_id,
        content=corpus.box_stl.read_bytes(),
    )

    assert upload.status_code == 201, upload.text
    jobs_resp = await authenticated_client.get("/api/jobs")
    jobs_list = jobs_resp.json()
    for step in PIPELINE_STEPS[BlobFormat.STL]:
        step_job = next(j for j in jobs_list if j["type"] == step)
        assert step_job["state"] == "done", (step, step_job)

    job_types = {j["type"] for j in jobs_list}
    assert job_types == {
        "store_to_backend",
        "render_assembly_thumb",
        *PIPELINE_STEPS[BlobFormat.STL],
    }


# ---------------------------------------------------------------------------
# retry_job's generalized pipeline-step branch.
# ---------------------------------------------------------------------------


async def _seed_failed_step_job(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, *, name: str
) -> tuple[str, int, str]:
    """Upload an STL (PIPELINE_STEPS patched to a single ``test_step``),
    forcing that step to fail on its first run. Returns
    ``(job_id, file_id, blob_hash)`` for the resulting FAILED job.
    """
    monkeypatch.setattr(pipeline, "PIPELINE_STEPS", {BlobFormat.STL: ("test_step",)})
    _step_state["outcome"] = RuntimeError("first attempt fails")

    created = await _create_model(authenticated_client, name)
    revision_id = created["current_revision"]["id"]
    upload = await _upload_stl(
        authenticated_client, model_id=created["id"], revision_id=revision_id
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["file_id"]
    blob_hash = upload.json()["blob_hash"]

    jobs_resp = await authenticated_client.get("/api/jobs")
    step_job = next(j for j in jobs_resp.json() if j["type"] == "test_step")
    assert step_job["state"] == "failed"
    return step_job["id"], file_id, blob_hash


async def test_retry_pipeline_step_redispatches_and_can_succeed(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, _file_id, _blob_hash = await _seed_failed_step_job(
        authenticated_client, monkeypatch, name="Retry Step Success Target"
    )
    _step_state["outcome"] = "done"

    response = await authenticated_client.post(f"/api/jobs/{job_id}/retry")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "done"


async def test_retry_pipeline_step_subject_file_gone_is_409(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    job_id, file_id, _blob_hash = await _seed_failed_step_job(
        authenticated_client, monkeypatch, name="Retry Step File Gone Target"
    )
    file = await db_session.get(File, file_id)
    await db_session.delete(file)
    await db_session.commit()

    response = await authenticated_client.post(f"/api/jobs/{job_id}/retry")

    assert response.status_code == 409
    assert "subject file no longer exists" in response.json()["detail"]


async def test_retry_pipeline_step_failing_again_ends_failed_not_queued(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, _file_id, _blob_hash = await _seed_failed_step_job(
        authenticated_client, monkeypatch, name="Retry Step Fails Again Target"
    )
    # _step_state["outcome"] is still the RuntimeError set by the seed helper
    # -- the retried run fails again.

    response = await authenticated_client.post(f"/api/jobs/{job_id}/retry")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "failed"


async def test_retry_unknown_pipeline_job_type_is_409(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    """A job ``type`` that's neither ``store_to_backend``, a name in
    ``PIPELINE_STEPS``, nor ``render_assembly_thumb`` (Task 6's own
    dedicated branch, covered separately in ``test_assembly_thumbs.py``)
    falls through to the generic "unknown job type" 409.
    """
    job = Job(
        type="totally_unrecognized_job_type",
        subject_type="revision",
        subject_id=1,
        state="failed",
        error="boom",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    response = await authenticated_client.post(f"/api/jobs/{job.id}/retry")

    assert response.status_code == 409
    assert "unknown job type" in response.json()["detail"]
