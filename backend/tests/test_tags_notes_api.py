"""Tags (get-or-create, list, remove) and notes (model-level + per-revision
CRUD, included inline in model/revision GETs) -- Task 5 brief.
"""

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("library_root")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------


async def test_add_tag_creates_and_attaches(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Tag Target")

    response = await authenticated_client.post(
        f"/api/models/{model['id']}/tags", json={"name": "keychain"}
    )

    assert response.status_code == 201
    assert response.json()["name"] == "keychain"

    detail = await authenticated_client.get(f"/api/models/{model['slug']}")
    assert detail.json()["tags"] == ["keychain"]

    tags = await authenticated_client.get("/api/tags")
    assert [t["name"] for t in tags.json()] == ["keychain"]


async def test_add_tag_is_get_or_create_across_models(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model_a = await _create_model(authenticated_client, "Tag Reuse A")
    model_b = await _create_model(authenticated_client, "Tag Reuse B")

    first = await authenticated_client.post(
        f"/api/models/{model_a['id']}/tags", json={"name": "shared"}
    )
    second = await authenticated_client.post(
        f"/api/models/{model_b['id']}/tags", json={"name": "shared"}
    )

    assert first.json()["id"] == second.json()["id"]

    tags = await authenticated_client.get("/api/tags")
    assert [t["name"] for t in tags.json()] == ["shared"]


async def test_add_tag_with_valid_color(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Colored Tag")

    response = await authenticated_client.post(
        f"/api/models/{model['id']}/tags", json={"name": "urgent", "color": "#ef4444"}
    )

    assert response.status_code == 201
    assert response.json()["color"] == "#ef4444"


async def test_add_tag_with_invalid_color_is_422(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Bad Color Tag")

    response = await authenticated_client.post(
        f"/api/models/{model['id']}/tags", json={"name": "urgent", "color": "not-a-color"}
    )

    assert response.status_code == 422


async def test_update_tag_color(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Recolor Tag")
    created = await authenticated_client.post(
        f"/api/models/{model['id']}/tags", json={"name": "recolor"}
    )
    tag_id = created.json()["id"]

    response = await authenticated_client.patch(f"/api/tags/{tag_id}", json={"color": "#14b8a6"})

    assert response.status_code == 200
    assert response.json()["color"] == "#14b8a6"

    tags = await authenticated_client.get("/api/tags")
    assert tags.json()[0]["color"] == "#14b8a6"


async def test_update_tag_color_invalid_is_422(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Recolor Bad Tag")
    created = await authenticated_client.post(
        f"/api/models/{model['id']}/tags", json={"name": "recolorbad"}
    )
    tag_id = created.json()["id"]

    response = await authenticated_client.patch(f"/api/tags/{tag_id}", json={"color": "nope"})

    assert response.status_code == 422


async def test_remove_tag_detaches_but_keeps_tag_row_for_other_models(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model_a = await _create_model(authenticated_client, "Remove Tag A")
    model_b = await _create_model(authenticated_client, "Remove Tag B")
    await authenticated_client.post(f"/api/models/{model_a['id']}/tags", json={"name": "shared"})
    await authenticated_client.post(f"/api/models/{model_b['id']}/tags", json={"name": "shared"})

    response = await authenticated_client.delete(f"/api/models/{model_a['id']}/tags/shared")
    assert response.status_code == 204

    detail_a = await authenticated_client.get(f"/api/models/{model_a['slug']}")
    detail_b = await authenticated_client.get(f"/api/models/{model_b['slug']}")
    assert detail_a.json()["tags"] == []
    assert detail_b.json()["tags"] == ["shared"]

    tags = await authenticated_client.get("/api/tags")
    assert [t["name"] for t in tags.json()] == ["shared"]


async def test_remove_tag_not_attached_is_404(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "No Tags Here")

    response = await authenticated_client.delete(f"/api/models/{model['id']}/tags/nonexistent")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


async def test_model_level_note_round_trip(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Notes Target")

    create = await authenticated_client.post(
        "/api/notes", json={"model_id": model["id"], "body": "printed at 0.2mm"}
    )
    assert create.status_code == 201
    note_id = create.json()["id"]

    detail = await authenticated_client.get(f"/api/models/{model['slug']}")
    assert [n["body"] for n in detail.json()["notes"]] == ["printed at 0.2mm"]
    # Model-level notes don't leak into the revision's own notes.
    assert detail.json()["current_revision"]["notes"] == []

    patch = await authenticated_client.patch(f"/api/notes/{note_id}", json={"body": "updated"})
    assert patch.status_code == 200
    assert patch.json()["body"] == "updated"

    delete = await authenticated_client.delete(f"/api/notes/{note_id}")
    assert delete.status_code == 204

    after = await authenticated_client.get(f"/api/models/{model['slug']}")
    assert after.json()["notes"] == []


async def test_revision_level_note_round_trip(authenticated_client: httpx.AsyncClient) -> None:
    model = await _create_model(authenticated_client, "Revision Notes Target")
    revision_id = model["current_revision"]["id"]

    create = await authenticated_client.post(
        "/api/notes",
        json={"model_id": model["id"], "revision_id": revision_id, "body": "first pass"},
    )
    assert create.status_code == 201

    revision_detail = await authenticated_client.get(f"/api/revisions/{revision_id}")
    assert [n["body"] for n in revision_detail.json()["notes"]] == ["first pass"]

    # Revision-level notes don't leak into the model-level notes list.
    model_detail = await authenticated_client.get(f"/api/models/{model['slug']}")
    assert model_detail.json()["notes"] == []


async def test_create_note_with_revision_not_belonging_to_model_is_404(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model_a = await _create_model(authenticated_client, "Note Model A")
    model_b = await _create_model(authenticated_client, "Note Model B")

    response = await authenticated_client.post(
        "/api/notes",
        json={
            "model_id": model_a["id"],
            "revision_id": model_b["current_revision"]["id"],
            "body": "mismatched",
        },
    )

    assert response.status_code == 404


async def test_create_note_unknown_model_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/notes", json={"model_id": 999999, "body": "orphan"}
    )

    assert response.status_code == 404
