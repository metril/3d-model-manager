"""Print queue (Branch 4 Task 1): an ordered "models to print" worklist.

``GET /queue``/``POST /queue``/``DELETE /queue/{id}``/``PATCH /queue/{id}``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import BlobFormat, BlobKind, PrinterKind, QueueState
from app.models.library import File, Model, PrintQueueEntry, Revision
from app.models.slicing import PrintRecipe, SlicerProfile

pytestmark = pytest.mark.usefixtures("library_root")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# append / idempotent re-add
# ---------------------------------------------------------------------------


async def test_enqueue_appends_at_end_with_incrementing_positions(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model_a = await _create_model(authenticated_client, "Queue A")
    model_b = await _create_model(authenticated_client, "Queue B")
    model_c = await _create_model(authenticated_client, "Queue C")

    for model in (model_a, model_b, model_c):
        response = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
        assert response.status_code == 201, response.text

    listing = await authenticated_client.get("/api/queue")
    assert listing.status_code == 200
    entries = listing.json()
    assert [e["position"] for e in entries] == [1, 2, 3]
    assert [e["model_id"] for e in entries] == [model_a["id"], model_b["id"], model_c["id"]]
    assert entries[0]["model"]["slug"] == model_a["slug"]


async def test_enqueue_unknown_model_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post("/api/queue", json={"model_id": 999999})

    assert response.status_code == 404


async def test_enqueue_already_queued_model_is_idempotent(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Queue Idempotent")

    first = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
    assert first.status_code == 201
    first_entry = first.json()

    second = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
    assert second.status_code == 200
    assert second.json() == first_entry

    listing = await authenticated_client.get("/api/queue")
    assert len(listing.json()) == 1


# ---------------------------------------------------------------------------
# delete + compaction
# ---------------------------------------------------------------------------


async def test_delete_entry_compacts_remaining_positions(
    authenticated_client: httpx.AsyncClient,
) -> None:
    models = [await _create_model(authenticated_client, f"Queue Delete {i}") for i in range(3)]
    entries = []
    for model in models:
        response = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
        entries.append(response.json())

    # Delete the middle entry (position 2).
    delete = await authenticated_client.delete(f"/api/queue/{entries[1]['id']}")
    assert delete.status_code == 204

    listing = await authenticated_client.get("/api/queue")
    remaining = listing.json()
    assert [e["position"] for e in remaining] == [1, 2]
    assert [e["model_id"] for e in remaining] == [models[0]["id"], models[2]["id"]]


async def test_delete_unknown_entry_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.delete("/api/queue/999999")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# reorder
# ---------------------------------------------------------------------------


async def test_patch_position_reorders_and_returns_full_list(
    authenticated_client: httpx.AsyncClient,
) -> None:
    models = [await _create_model(authenticated_client, f"Queue Reorder {i}") for i in range(3)]
    entries = []
    for model in models:
        response = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
        entries.append(response.json())

    # Move the last entry (position 3) to the front.
    response = await authenticated_client.patch(
        f"/api/queue/{entries[2]['id']}", json={"position": 1}
    )
    assert response.status_code == 200
    reordered = response.json()
    assert [e["model_id"] for e in reordered] == [
        models[2]["id"],
        models[0]["id"],
        models[1]["id"],
    ]
    assert [e["position"] for e in reordered] == [1, 2, 3]


async def test_patch_position_clamps_to_valid_range(
    authenticated_client: httpx.AsyncClient,
) -> None:
    models = [await _create_model(authenticated_client, f"Queue Clamp {i}") for i in range(2)]
    entries = []
    for model in models:
        response = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
        entries.append(response.json())

    too_high = await authenticated_client.patch(
        f"/api/queue/{entries[0]['id']}", json={"position": 999}
    )
    assert too_high.status_code == 200
    assert [e["model_id"] for e in too_high.json()] == [models[1]["id"], models[0]["id"]]

    too_low = await authenticated_client.patch(
        f"/api/queue/{entries[0]['id']}", json={"position": -5}
    )
    assert too_low.status_code == 200
    assert [e["model_id"] for e in too_low.json()] == [models[0]["id"], models[1]["id"]]


async def test_patch_unknown_entry_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.patch("/api/queue/999999", json={"position": 1})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# model delete cascades the queue entry
# ---------------------------------------------------------------------------


async def test_model_hard_delete_cascades_the_queue_entry(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M1 only supports soft-delete (archive) via the API -- the queue's
    ``ON DELETE CASCADE`` is exercised at the DB level directly (a raw SQL
    DELETE, since there is no hard-delete endpoint to drive it through HTTP).
    """
    model = await _create_model(authenticated_client, "Queue Cascade")
    enqueue = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
    assert enqueue.status_code == 201
    entry_id = enqueue.json()["id"]

    await db_session.execute(text("DELETE FROM models WHERE id = :id"), {"id": model["id"]})
    await db_session.commit()

    remaining = (
        await db_session.execute(select(PrintQueueEntry).where(PrintQueueEntry.id == entry_id))
    ).scalar_one_or_none()
    assert remaining is None


# ---------------------------------------------------------------------------
# printable_file (Round 8 Task 3)
# ---------------------------------------------------------------------------


async def test_printable_file_is_null_for_stl_only_model(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_data = await _create_model(authenticated_client, "Queue STL Only")
    model = await db_session.get(Model, model_data["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"stl-bytes")

    response = await authenticated_client.post("/api/queue", json={"model_id": model.id})
    assert response.status_code == 201, response.text
    assert response.json()["printable_file"] is None

    listing = await authenticated_client.get("/api/queue")
    assert listing.json()[0]["printable_file"] is None


async def test_printable_file_is_populated_for_gcode_3mf_model(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_data = await _create_model(authenticated_client, "Queue Sliced")
    model = await db_session.get(Model, model_data["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    sliced = await seed_file(
        model,
        revision,
        "print.gcode.3mf",
        b"sliced-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )

    enqueue = await authenticated_client.post("/api/queue", json={"model_id": model.id})
    assert enqueue.status_code == 201, enqueue.text
    printable = enqueue.json()["printable_file"]
    assert printable is not None
    assert printable["id"] == sliced.id
    assert printable["format"] == "gcode_3mf"

    listing = await authenticated_client.get("/api/queue")
    assert listing.json()[0]["printable_file"]["id"] == sliced.id

    reorder = await authenticated_client.patch(
        f"/api/queue/{enqueue.json()['id']}", json={"position": 1}
    )
    assert reorder.status_code == 200
    assert reorder.json()[0]["printable_file"]["id"] == sliced.id


async def test_printable_file_is_the_newest_gcode_3mf_when_more_than_one(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_data = await _create_model(authenticated_client, "Queue Two Sliced Files")
    model = await db_session.get(Model, model_data["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    older = await seed_file(
        model,
        revision,
        "a.gcode.3mf",
        b"a-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    newer = await seed_file(
        model,
        revision,
        "b.gcode.3mf",
        b"b-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    assert newer.id > older.id  # sanity: insertion order is the "newest" signal

    enqueue = await authenticated_client.post("/api/queue", json={"model_id": model.id})
    assert enqueue.status_code == 201, enqueue.text
    assert enqueue.json()["printable_file"]["id"] == newer.id


# ---------------------------------------------------------------------------
# P6 Task 6: per-file queue items (plate/printer/recipe, retry, visibility)
# ---------------------------------------------------------------------------


async def _model_and_revision(db_session: AsyncSession, slug: str) -> tuple[Model, Revision]:
    model = Model(slug=slug, name=slug)
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(revision)
    await db_session.commit()
    await db_session.refresh(revision)
    return model, revision


async def _recipe(db_session: AsyncSession, name: str = "r1") -> PrintRecipe:
    from app.models.enums import ProfileKind

    printer_profile = SlicerProfile(name=f"{name}-printer", kind=ProfileKind.PRINTER, data={})
    process_profile = SlicerProfile(name=f"{name}-process", kind=ProfileKind.PROCESS, data={})
    db_session.add_all([printer_profile, process_profile])
    await db_session.flush()
    recipe = PrintRecipe(
        name=name,
        printer_profile_id=printer_profile.id,
        process_profile_id=process_profile.id,
        filament_profile_ids=[None],
        bed_type="textured_plate",
        options={},
    )
    db_session.add(recipe)
    await db_session.commit()
    await db_session.refresh(recipe)
    return recipe


async def _printer(db_session: AsyncSession, name: str = "P1") -> int:
    from app.models import Printer

    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.1", serial=name, access_code_enc="x"
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p.id


async def test_bare_enqueue_still_idempotent(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Legacy Bare")
    first = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
    second = await authenticated_client.post("/api/queue", json={"model_id": model["id"]})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


async def test_file_enqueue_persists_plate_printer_mapping(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _model_and_revision(db_session, "file-enqueue")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    pid = await _printer(db_session, "FileEnqueuePrinter")

    r = await authenticated_client.post(
        "/api/queue",
        json={
            "file_id": f.id,
            "plate": 2,
            "printer_id": pid,
            "copies": 3,
            "ams_mapping": [0, 1],
            "options": {"timelapse": True},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file_id"] == f.id
    assert body["plate"] == 2
    assert body["printer_id"] == pid
    assert body["copies"] == 3
    assert body["state"] == "ready"
    assert body["sliced_file_id"] == f.id
    assert body["file_name"] == "job.gcode.3mf"

    entry = await db_session.get(PrintQueueEntry, body["id"])
    assert entry.ams_mapping == [0, 1]
    assert entry.print_options["timelapse"] is True


async def test_file_enqueue_with_recipe_is_waiting_and_accepts_unsliced(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _model_and_revision(db_session, "recipe-enqueue")
    f = await seed_file(
        model,
        revision,
        "part.stl",
        b"stl-bytes",
        blob_format=BlobFormat.STL,
        blob_kind=BlobKind.MESH,
    )
    recipe = await _recipe(db_session)

    r = await authenticated_client.post(
        "/api/queue", json={"file_id": f.id, "recipe_id": recipe.id}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "waiting"
    assert body["sliced_file_id"] is None
    assert body["recipe_id"] == recipe.id
    assert body["recipe_name"] == recipe.name


async def test_as_sliced_non_gcode_is_422(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _model_and_revision(db_session, "not-sliced")
    f = await seed_file(
        model,
        revision,
        "part.stl",
        b"stl-bytes",
        blob_format=BlobFormat.STL,
        blob_kind=BlobKind.MESH,
    )
    r = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    assert r.status_code == 422


async def test_front_inserts_at_position_one(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a = await _create_model(authenticated_client, "Front A")
    await authenticated_client.post("/api/queue", json={"model_id": model_a["id"]})

    model_b, revision_b = await _model_and_revision(db_session, "front-b")
    f = await seed_file(
        model_b,
        revision_b,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    r = await authenticated_client.post("/api/queue", json={"file_id": f.id, "front": True})
    assert r.status_code == 201, r.text
    assert r.json()["position"] == 1

    listing = (await authenticated_client.get("/api/queue")).json()
    assert listing[0]["id"] == r.json()["id"]
    assert listing[1]["model_id"] == model_a["id"]


async def test_non_bare_enqueue_never_idempotent(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _model_and_revision(db_session, "dup-file")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    first = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    second = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


async def test_retry_endpoint_states(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _model_and_revision(db_session, "retry")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    enqueue = await authenticated_client.post("/api/queue", json={"file_id": f.id})
    entry_id = enqueue.json()["id"]

    # not failed/held yet -> 409
    not_yet = await authenticated_client.post(f"/api/queue/{entry_id}/retry")
    assert not_yet.status_code == 409

    entry = await db_session.get(PrintQueueEntry, entry_id)
    entry.state = QueueState.FAILED
    entry.attempts = 3
    entry.last_error = "boom"
    await db_session.commit()

    retried = await authenticated_client.post(f"/api/queue/{entry_id}/retry")
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["state"] == "ready"
    assert body["attempts"] == 0
    assert body["last_error"] is None

    entry = await db_session.get(PrintQueueEntry, entry_id)
    entry.state = QueueState.HELD
    await db_session.commit()
    retried_held = await authenticated_client.post(f"/api/queue/{entry_id}/retry")
    assert retried_held.status_code == 200


async def test_retry_unknown_entry_is_404(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.post("/api/queue/999999/retry")
    assert r.status_code == 404


async def test_visibility_hides_items_on_invisible_printers(
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
    make_user,
    make_group,
    login_as,
) -> None:
    model, revision = await _model_and_revision(db_session, "visibility")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    visible_pid = await _printer(db_session, "VisiblePrinter")
    hidden_pid = await _printer(db_session, "HiddenPrinter")

    member = await make_user("member")
    await make_group(
        "printer-folks",
        permissions=["printers:print"],
        members=[member],
        printer_access={visible_pid: (True, False, False)},
    )
    client = await login_as(member)

    visible_entry = await client.post(
        "/api/queue", json={"file_id": f.id, "printer_id": visible_pid}
    )
    assert visible_entry.status_code == 201, visible_entry.text

    hidden_entry_response = await client.post(
        "/api/queue", json={"file_id": f.id, "printer_id": hidden_pid}
    )
    assert hidden_entry_response.status_code == 403

    listing = await client.get("/api/queue")
    ids = [e["id"] for e in listing.json()]
    assert visible_entry.json()["id"] in ids


async def test_visibility_hides_items_on_invisible_assigned_printer(
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
    make_user,
    make_group,
    login_as,
) -> None:
    """M3: an unpinned entry (no explicit `printer_id`) the dispatcher has
    already assigned to a printer outside the viewer's ACL must also be
    hidden -- not just entries with an explicit, invisible `printer_id`."""
    model, revision = await _model_and_revision(db_session, "visibility-assigned")
    f = await seed_file(
        model,
        revision,
        "job.gcode.3mf",
        b"gc",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    hidden_pid = await _printer(db_session, "HiddenAssignedPrinter")

    # No API path sets `assigned_printer_id` -- that's the dispatcher's own
    # bookkeeping -- so seed the entry directly.
    entry = PrintQueueEntry(
        model_id=model.id,
        file_id=f.id,
        sliced_file_id=f.id,
        state=QueueState.DISPATCHED,
        position=1,
        assigned_printer_id=hidden_pid,
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    member = await make_user("member")
    await make_group("assigned-visibility-folks", permissions=["printers:print"], members=[member])
    client = await login_as(member)

    listing = await client.get("/api/queue")
    ids = [e["id"] for e in listing.json()]
    assert entry.id not in ids
