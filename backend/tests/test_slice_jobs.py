"""Slice-only jobs (`POST /files/{id}/slice`, `GET`/`DELETE /slice-jobs/{id}`),
the queue's `cancel-slice`, and the `slice_job`/`slice_file` task paths that
persist `last_log` / job state. Celery runs eagerly in tests, so creating a job
runs the task inline against a fake sidecar client."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import BlobFormat, BlobKind, QueueState
from app.models.library import File, Model, PrintQueueEntry, Revision
from app.models.slicing import PrintRecipe, SliceJob, SlicerProfile
from app.services import slice_jobs
from app.services.slicer_client import SlicerCancelled, SlicerError
from app.tasks import slicing

pytestmark = pytest.mark.usefixtures("library_root")


class FakeClient:
    def __init__(self, result: bytes | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.job_ids: list[str | None] = []

    def slice(self, **kwargs) -> bytes:
        self.job_ids.append(kwargs.get("job_id"))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture(autouse=True)
def _fetch_stub(monkeypatch: pytest.MonkeyPatch):
    from app.services import derivatives

    def _fake_fetch(session, settings, blob_hash, dest_dir, suffix):
        dest = dest_dir / f"blob{suffix}"
        dest.write_bytes(b"fake-source")
        return dest

    monkeypatch.setattr(derivatives, "fetch_blob_to_temp", _fake_fetch)


async def _setup(db: AsyncSession, seed_file: Callable[..., Awaitable[File]], slug: str):
    from app.models.enums import ProfileKind

    model = Model(slug=slug, name=slug)
    db.add(model)
    await db.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db.add(rev)
    await db.commit()
    await db.refresh(rev)
    f = await seed_file(
        model, rev, "part.stl", b"stl", blob_format=BlobFormat.STL, blob_kind=BlobKind.MESH
    )
    pp = SlicerProfile(
        name=f"{slug}-p", kind=ProfileKind.PRINTER, printer_model="Bambu Lab A1 mini", data={}
    )
    pr = SlicerProfile(name=f"{slug}-q", kind=ProfileKind.PROCESS, data={})
    db.add_all([pp, pr])
    await db.flush()
    recipe = PrintRecipe(
        name=f"{slug}-r",
        printer_profile_id=pp.id,
        process_profile_id=pr.id,
        filament_profile_ids=[None],
        bed_type="textured_plate",
        options={},
    )
    db.add(recipe)
    await db.commit()
    await db.refresh(recipe)
    await db.refresh(f)
    return model, f, recipe


async def test_start_slice_disabled_and_validation(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    _, f, recipe = await _setup(db_session, seed_file, "sj-val")
    r = await authenticated_client.post(f"/api/files/{f.id}/slice", json={"recipe_id": recipe.id})
    assert r.status_code == 409 and r.json()["detail"] == "slicer_disabled"

    from tests.server_helpers import set_server

    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    r = await authenticated_client.post("/api/files/999999/slice", json={"recipe_id": recipe.id})
    assert r.status_code == 404
    r = await authenticated_client.post(f"/api/files/{f.id}/slice", json={"recipe_id": 999999})
    assert r.status_code == 404
    assert (await authenticated_client.get(f"/api/slice-jobs/{uuid.uuid4()}")).status_code == 404


async def test_slice_job_success_roundtrip(
    authenticated_client: httpx.AsyncClient,
    db_session,
    seed_file,
    monkeypatch: pytest.MonkeyPatch,
    sliced_3mf_bytes: bytes,
) -> None:
    from tests.server_helpers import set_server

    _, f, recipe = await _setup(db_session, seed_file, "sj-ok")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    fake = FakeClient(result=sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake)

    r = await authenticated_client.post(
        f"/api/files/{f.id}/slice", json={"recipe_id": recipe.id, "plate": 1}
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    body = (await authenticated_client.get(f"/api/slice-jobs/{job_id}")).json()
    assert body["state"] == "done", body
    assert body["sliced_file_id"] is not None and body["file_id"] == f.id
    assert body["model_id"] is not None and body["model_slug"] == "sj-ok"
    assert body["file_name"] == "part.stl"
    assert body["meta"]["plate_count"] >= 1 and "plates" in body["meta"]
    assert fake.job_ids == [f"sj-{job_id}"]

    sliced = await db_session.get(File, body["sliced_file_id"], populate_existing=True)
    assert sliced.rel_path.endswith(".gcode.3mf") and f"(s{job_id.replace('-', '')[:8]} p1)" in (
        sliced.rel_path
    )


async def test_slice_job_failure_keeps_log_tail(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    from tests.server_helpers import set_server

    _, f, recipe = await _setup(db_session, seed_file, "sj-fail")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    err = SlicerError("slicer returned 422", 422, "boom on stderr")
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: FakeClient(error=err))

    r = await authenticated_client.post(f"/api/files/{f.id}/slice", json={"recipe_id": recipe.id})
    body = (await authenticated_client.get(f"/api/slice-jobs/{r.json()['id']}")).json()
    assert body["state"] == "failed"
    assert "422" in body["error"] and body["log_tail"] == "boom on stderr"
    assert body["sliced_file_id"] is None


async def test_cancel_slice_job(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    _, f, recipe = await _setup(db_session, seed_file, "sj-cancel")
    job = SliceJob(file_id=f.id, recipe_id=recipe.id, state="running", celery_id="tid")
    db_session.add(job)
    await db_session.commit()

    calls = []

    async def fake_stop(db, settings, sidecar_id, task_id):
        calls.append((sidecar_id, task_id))

    monkeypatch.setattr(slice_jobs, "stop_slice", fake_stop)
    r = await authenticated_client.delete(f"/api/slice-jobs/{job.id}")
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert calls == [(f"sj-{job.id}", "tid")]
    # idempotent on a terminal job: no second stop
    again = await authenticated_client.delete(f"/api/slice-jobs/{job.id}")
    assert again.json()["state"] == "cancelled" and len(calls) == 1


async def test_cancelled_job_result_is_discarded(db_session, seed_file) -> None:
    from app.config import get_settings
    from tests.server_helpers import set_server

    _, f, recipe = await _setup(db_session, seed_file, "sj-discard")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    job = SliceJob(file_id=f.id, recipe_id=recipe.id, state="cancelled")
    db_session.add(job)
    await db_session.commit()
    slicing._finish_slice_job(get_settings(), job.id, state="failed", error="late")
    await db_session.refresh(job)
    assert job.state == "cancelled" and job.error is None


async def test_queue_cancel_slice(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    _, f, recipe = await _setup(db_session, seed_file, "q-cancel")
    rev = await db_session.get(Revision, f.revision_id)
    entry = PrintQueueEntry(
        model_id=rev.model_id,
        position=1,
        file_id=f.id,
        recipe_id=recipe.id,
        state=QueueState.SLICING,
        slice_task_id="tid-q",
    )
    db_session.add(entry)
    await db_session.commit()

    calls = []

    async def fake_stop(db, settings, sidecar_id, task_id):
        calls.append((sidecar_id, task_id))

    monkeypatch.setattr(slice_jobs, "stop_slice", fake_stop)
    r = await authenticated_client.post(f"/api/queue/{entry.id}/cancel-slice")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "failed" and r.json()["last_error"] == "cancelled"
    assert calls == [(f"q{entry.id}", "tid-q")]
    # no longer slicing -> 409
    assert (
        await authenticated_client.post(f"/api/queue/{entry.id}/cancel-slice")
    ).status_code == 409


async def test_queue_failure_exposes_last_log_and_sliced_meta(
    authenticated_client: httpx.AsyncClient, db_session, seed_file, monkeypatch
) -> None:
    _, f, recipe = await _setup(db_session, seed_file, "q-log")
    rev = await db_session.get(Revision, f.revision_id)
    entry = PrintQueueEntry(
        model_id=rev.model_id,
        position=1,
        file_id=f.id,
        recipe_id=recipe.id,
        state=QueueState.SLICING,
    )
    db_session.add(entry)
    await db_session.commit()
    monkeypatch.setattr(
        slicing,
        "client_from_config",
        lambda cfg: FakeClient(error=SlicerError("slicer returned 422", 422, "tail text")),
    )
    slicing.slice_file(entry.id)

    rows = (await authenticated_client.get("/api/queue")).json()
    got = next(r for r in rows if r["id"] == entry.id)
    assert got["last_log"] == "tail text" and got["state"] == "waiting"
    assert got["sliced_meta"] is None


async def test_queue_cancelled_slice_task_leaves_entry(db_session, seed_file, monkeypatch) -> None:
    _, f, recipe = await _setup(db_session, seed_file, "q-cx")
    rev = await db_session.get(Revision, f.revision_id)
    entry = PrintQueueEntry(
        model_id=rev.model_id,
        position=1,
        file_id=f.id,
        recipe_id=recipe.id,
        state=QueueState.SLICING,
    )
    db_session.add(entry)
    await db_session.commit()
    monkeypatch.setattr(
        slicing, "client_from_config", lambda cfg: FakeClient(error=SlicerCancelled("c", 499))
    )
    slicing.slice_file(entry.id)
    await db_session.refresh(entry)
    assert entry.state == QueueState.FAILED and entry.last_error == "cancelled"
    assert entry.attempts == 0


def test_sidecar_ids_are_stable() -> None:
    assert slicing.queue_job_id(7) == "q7"
    jid = uuid.uuid4()
    assert slicing.slice_job_sidecar_id(jid) == f"sj-{jid}"
