"""Slicing with the model file's own process settings (``file_settings``):
no process profile reaches the slicer; the recipe's printer + filament still do."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.models.library import File, PrintQueueEntry
from app.models.slicing import SliceJob
from app.tasks import slicing
from tests.server_helpers import set_server
from tests.test_slice_jobs import FakeClient, _fetch_stub, _setup  # noqa: F401

pytestmark = pytest.mark.usefixtures("library_root")


class _SpyClient(FakeClient):
    def slice(self, **kwargs) -> bytes:
        self.kwargs = kwargs
        return super().slice(**kwargs)


async def test_slice_job_file_settings_sends_no_process_profile(
    authenticated_client: httpx.AsyncClient,
    db_session,
    seed_file,
    monkeypatch: pytest.MonkeyPatch,
    sliced_3mf_bytes: bytes,
) -> None:
    _, f, recipe = await _setup(db_session, seed_file, "sj-fs")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    spy = _SpyClient(result=sliced_3mf_bytes)
    monkeypatch.setattr(slicing, "client_from_config", lambda cfg: spy)

    r = await authenticated_client.post(
        f"/api/files/{f.id}/slice",
        json={"recipe_id": recipe.id, "plate": 1, "file_settings": True},
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    assert spy.kwargs["process_profile"] is None
    assert spy.kwargs["printer_profile"] is not None
    body = (await authenticated_client.get(f"/api/slice-jobs/{job_id}")).json()
    assert body["state"] == "done", body
    job = await db_session.get(SliceJob, uuid.UUID(job_id))
    assert job.options == {"file_settings": True}
    new_file = await db_session.get(File, body["sliced_file_id"])
    assert ", file settings (" in new_file.rel_path

    # The default still sends the recipe's process profile.
    r = await authenticated_client.post(
        f"/api/files/{f.id}/slice", json={"recipe_id": recipe.id, "plate": 1}
    )
    assert r.status_code == 201, r.text
    body2 = (await authenticated_client.get(f"/api/slice-jobs/{r.json()['id']}")).json()
    assert body2["state"] == "done", body2
    assert spy.kwargs["process_profile"] is not None


async def test_queue_plates_file_settings_reaches_entries(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    from sqlalchemy import select

    _, f, recipe = await _setup(db_session, seed_file, "q-fs-plates")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    r = await authenticated_client.post(
        "/api/queue/plates",
        json={
            "file_id": f.id,
            "plates": [1],
            "recipe_id": recipe.id,
            "file_settings": True,
            "force": True,
        },
    )
    assert r.status_code in (200, 201), r.text
    rows = (await db_session.execute(select(PrintQueueEntry))).scalars().all()
    assert rows and all((e.slice_options or {}).get("file_settings") for e in rows)


async def test_queue_enqueue_file_settings_sets_slice_options(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    _, f, recipe = await _setup(db_session, seed_file, "q-fs")
    await set_server(slicer_enabled=True, slicer_url="http://s:1")
    r = await authenticated_client.post(
        "/api/queue",
        json={"file_id": f.id, "recipe_id": recipe.id, "file_settings": True, "force": True},
    )
    assert r.status_code in (200, 201), r.text
    entry = await db_session.get(PrintQueueEntry, r.json()["id"])
    assert entry.slice_options == {"file_settings": True}
