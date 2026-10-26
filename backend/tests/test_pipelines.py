"""Slicer pipelines: CRUD + validation, slice-only runs (pre-step option
overrides, rename post-step, run history) and queue runs (slice_options reach
the queue's own slice)."""

from __future__ import annotations

import httpx
import pytest

from app.models.enums import BlobFormat, BlobKind, ProfileKind, QueueState
from app.models.library import File, Model, PrintQueueEntry, Revision
from app.models.slicing import PrintRecipe, SlicerProfile
from app.services import pipelines
from app.tasks import slicing
from tests.server_helpers import set_server

pytestmark = pytest.mark.usefixtures("library_root")


class FakeClient:
    def __init__(self, result: bytes):
        self.result = result
        self.calls: list[dict] = []

    def slice(self, **kwargs) -> bytes:
        self.calls.append(kwargs)
        return self.result


@pytest.fixture(autouse=True)
def _fetch_stub(monkeypatch: pytest.MonkeyPatch):
    from app.services import derivatives

    def _fake_fetch(session, settings, blob_hash, dest_dir, suffix):
        dest = dest_dir / f"blob{suffix}"
        dest.write_bytes(b"fake-source")
        return dest

    monkeypatch.setattr(derivatives, "fetch_blob_to_temp", _fake_fetch)


async def _setup(db, seed_file, slug: str):
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
        options={"auto_orient": False},
    )
    db.add(recipe)
    await db.commit()
    await db.refresh(recipe)
    await db.refresh(f)
    return f, recipe


async def test_pipeline_crud_and_validation(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    _, recipe = await _setup(db_session, seed_file, "pl-crud")
    body = {
        "name": "Orient+rename",
        "recipe_id": recipe.id,
        "steps": [{"kind": "orient"}, {"kind": "rename", "template": "{name}-{plate}"}],
    }
    r = await authenticated_client.post("/api/slicer-pipelines", json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["recipe_name"] == recipe.name

    # duplicate name, unknown recipe, exclusive steps, repeated kind
    assert (await authenticated_client.post("/api/slicer-pipelines", json=body)).status_code == 409
    assert (
        await authenticated_client.post("/api/slicer-pipelines", json={**body, "recipe_id": 9999})
    ).status_code == 404
    bad = {**body, "name": "x", "steps": [{"kind": "rename", "template": "a"}, {"kind": "queue"}]}
    assert (await authenticated_client.post("/api/slicer-pipelines", json=bad)).status_code == 422
    dup = {**body, "name": "y", "steps": [{"kind": "orient"}, {"kind": "orient"}]}
    assert (await authenticated_client.post("/api/slicer-pipelines", json=dup)).status_code == 422
    path = {**body, "name": "z", "steps": [{"kind": "rename", "template": "a/b"}]}
    assert (await authenticated_client.post("/api/slicer-pipelines", json=path)).status_code == 422

    r = await authenticated_client.patch(
        f"/api/slicer-pipelines/{pid}", json={"steps": [{"kind": "arrange"}], "plate": 2}
    )
    assert r.status_code == 200 and r.json()["steps"] == [{"kind": "arrange"}]
    assert r.json()["plate"] == 2
    assert [p["id"] for p in (await authenticated_client.get("/api/slicer-pipelines")).json()] == [
        pid
    ]

    # a recipe used by a pipeline can't be deleted
    r = await authenticated_client.delete(f"/api/recipes/{recipe.id}")
    assert r.status_code == 409 and "pipeline" in r.json()["detail"]

    assert (await authenticated_client.delete(f"/api/slicer-pipelines/{pid}")).status_code == 204
    assert (await authenticated_client.get(f"/api/slicer-pipelines/{pid}")).status_code == 404


async def test_run_slice_only_applies_overrides_and_rename(
    authenticated_client: httpx.AsyncClient,
    db_session,
    seed_file,
    monkeypatch: pytest.MonkeyPatch,
    sliced_3mf_bytes: bytes,
) -> None:
    f, recipe = await _setup(db_session, seed_file, "pl-run")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    fake = FakeClient(sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake)
    r = await authenticated_client.post(
        "/api/slicer-pipelines",
        json={
            "name": "p",
            "recipe_id": recipe.id,
            "steps": [
                {"kind": "orient"},
                {"kind": "arrange"},
                {"kind": "rename", "template": "{name} [{pipeline}] p{plate}"},
            ],
        },
    )
    pid = r.json()["id"]

    r = await authenticated_client.post(
        f"/api/slicer-pipelines/{pid}/run", json={"file_id": f.id, "plate": 1}
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["kind"] == "slice" and run["queue_entry"] is None
    assert run["slice_job"]["pipeline_id"] == pid

    opts = fake.calls[0]["options"]
    assert opts.auto_orient is True and opts.arrange is True  # recipe says orient off

    job = (await authenticated_client.get(f"/api/slice-jobs/{run['slice_job']['id']}")).json()
    assert job["state"] == "done", job
    sliced = await db_session.get(File, job["sliced_file_id"], populate_existing=True)
    assert sliced.rel_path == "part [p] p1.gcode.3mf"

    runs = (await authenticated_client.get(f"/api/slicer-pipelines/{pid}/runs")).json()
    assert [x["id"] for x in runs] == [job["id"]]


async def test_run_with_queue_step_carries_slice_options(
    authenticated_client: httpx.AsyncClient,
    db_session,
    seed_file,
    monkeypatch: pytest.MonkeyPatch,
    sliced_3mf_bytes: bytes,
) -> None:
    f, recipe = await _setup(db_session, seed_file, "pl-queue")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    r = await authenticated_client.post(
        "/api/slicer-pipelines",
        json={
            "name": "q",
            "recipe_id": recipe.id,
            "steps": [{"kind": "arrange"}, {"kind": "queue", "copies": 2, "manual_start": True}],
        },
    )
    pid = r.json()["id"]
    r = await authenticated_client.post(f"/api/slicer-pipelines/{pid}/run", json={"file_id": f.id})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["kind"] == "queue" and run["slice_job"] is None
    entry_id = run["queue_entry"]["id"]

    entry = await db_session.get(PrintQueueEntry, entry_id, populate_existing=True)
    assert entry.slice_options == {"arrange": True}
    assert entry.copies == 2 and entry.manual_start is True and entry.recipe_id == recipe.id

    # the queue's own slice applies the overrides
    fake = FakeClient(sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: fake)
    entry.state = QueueState.SLICING
    await db_session.commit()
    slicing.slice_file(entry_id)
    assert fake.calls and fake.calls[0]["options"].arrange is True


async def test_run_unknown_pipeline_and_bad_printer(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    f, recipe = await _setup(db_session, seed_file, "pl-bad")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    r = await authenticated_client.post("/api/slicer-pipelines/9999/run", json={"file_id": f.id})
    assert r.status_code == 404
    r = await authenticated_client.post(
        "/api/slicer-pipelines",
        json={
            "name": "bp",
            "recipe_id": recipe.id,
            "steps": [{"kind": "queue", "printer_id": 9999}],
        },
    )
    pid = r.json()["id"]
    r = await authenticated_client.post(f"/api/slicer-pipelines/{pid}/run", json={"file_id": f.id})
    assert r.status_code == 404 and "printer" in r.json()["detail"]


def test_render_name_sanitizes() -> None:
    assert (
        pipelines.render_name(
            "{name}: {recipe}/x.gcode.3mf", name="a", recipe="r", pipeline="p", plate=2
        )
        == "a_ r_x.gcode.3mf"
    )
    assert (
        pipelines.render_name("  ", name="orig", recipe="", pipeline="", plate=1)
        == "orig.gcode.3mf"
    )
    assert pipelines.option_overrides([{"kind": "orient"}, {"kind": "rename"}]) == {
        "auto_orient": True
    }
