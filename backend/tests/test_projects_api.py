"""Projects: CRUD + project assignment + manufacturing status & quantity tracking."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_project(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/projects",
        json={"name": "Voron 2.4", "description": "Parts for Voron build", "color": "red"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Voron 2.4"
    assert body["slug"] == "voron-2-4"
    assert body["description"] == "Parts for Voron build"
    assert body["color"] == "red"
    assert body["model_count"] == 0
    assert body["total_quantity_target"] == 0
    assert body["total_quantity_printed"] == 0
    assert body["progress_pct"] == 0.0


async def test_create_project_duplicate_name_is_409(
    authenticated_client: httpx.AsyncClient,
) -> None:
    await authenticated_client.post("/api/projects", json={"name": "Duplicate Project"})
    response = await authenticated_client.post("/api/projects", json={"name": "Duplicate Project"})
    assert response.status_code == 409


async def test_update_project(authenticated_client: httpx.AsyncClient) -> None:
    created = (
        await authenticated_client.post(
            "/api/projects", json={"name": "Initial Name", "color": "blue"}
        )
    ).json()

    response = await authenticated_client.patch(
        f"/api/projects/{created['id']}",
        json={"name": "Updated Name", "color": "teal", "description": "Updated desc"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Updated Name"
    assert body["slug"] == "updated-name"
    assert body["color"] == "teal"
    assert body["description"] == "Updated desc"


async def test_model_project_assignment_and_progress_tracking(
    authenticated_client: httpx.AsyncClient,
) -> None:
    project = (
        await authenticated_client.post(
            "/api/projects", json={"name": "Enclosure Project", "color": "indigo"}
        )
    ).json()
    project_id = project["id"]

    model_a = await _create_model(authenticated_client, "Front Panel")
    model_b = await _create_model(authenticated_client, "Back Panel")

    # Patch model A: assign to project, target=4, printed=2, status=printing
    patch_resp = await authenticated_client.patch(
        f"/api/models/{model_a['slug']}",
        json={
            "project_id": project_id,
            "quantity_target": 4,
            "quantity_printed": 2,
            "print_status": "printing",
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    model_a_data = patch_resp.json()
    assert model_a_data["project_id"] == project_id
    assert model_a_data["project"]["name"] == "Enclosure Project"
    assert model_a_data["quantity_target"] == 4
    assert model_a_data["quantity_printed"] == 2
    assert model_a_data["print_status"] == "printing"

    # Patch model B: assign to project, target=6, printed=6, status=printed
    await authenticated_client.patch(
        f"/api/models/{model_b['slug']}",
        json={
            "project_id": project_id,
            "quantity_target": 6,
            "quantity_printed": 6,
            "print_status": "printed",
        },
    )

    # Check project aggregate stats: 2 models, target=10, printed=8, progress=80%
    proj_resp = await authenticated_client.get(f"/api/projects/{project_id}")
    assert proj_resp.status_code == 200
    proj_data = proj_resp.json()
    assert proj_data["model_count"] == 2
    assert proj_data["total_quantity_target"] == 10
    assert proj_data["total_quantity_printed"] == 8
    assert proj_data["progress_pct"] == 80.0

    # Filter models by project
    filter_proj_resp = await authenticated_client.get(f"/api/models?project={project_id}")
    assert filter_proj_resp.status_code == 200
    assert len(filter_proj_resp.json()["items"]) == 2

    # Filter models by print_status
    filter_status_resp = await authenticated_client.get("/api/models?print_status=printed")
    assert filter_status_resp.status_code == 200
    printed_items = filter_status_resp.json()["items"]
    assert len(printed_items) == 1
    assert printed_items[0]["name"] == "Back Panel"

    # Test bulk assign
    model_c = await _create_model(authenticated_client, "Hinge Left")
    model_d = await _create_model(authenticated_client, "Hinge Right")
    bulk_resp = await authenticated_client.post(
        "/api/models/bulk",
        json={
            "ids": [model_c["id"], model_d["id"]],
            "project_id": project_id,
            "print_status": "to_print",
        },
    )
    assert bulk_resp.status_code == 200
    assert bulk_resp.json()["updated"] == 2

    # Verify models C and D have project and print_status
    c_detail = (await authenticated_client.get(f"/api/models/{model_c['slug']}")).json()
    assert c_detail["project_id"] == project_id
    assert c_detail["print_status"] == "to_print"

    # Delete project sets models.project_id to NULL
    del_resp = await authenticated_client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code == 204

    # Verify model A now has project_id = None
    refreshed_a = (await authenticated_client.get(f"/api/models/{model_a['slug']}")).json()
    assert refreshed_a["project_id"] is None
    assert refreshed_a["project"] is None
    # But print status & quantities remain intact
    assert refreshed_a["quantity_target"] == 4
    assert refreshed_a["quantity_printed"] == 2
    assert refreshed_a["print_status"] == "printing"


async def test_filter_models_project_root_vs_assigned(
    authenticated_client: httpx.AsyncClient,
) -> None:
    project = (
        await authenticated_client.post(
            "/api/projects", json={"name": "Root Test Project", "color": "teal"}
        )
    ).json()
    proj_id = project["id"]

    model_in_project = await _create_model(authenticated_client, "Part In Folder")
    await _create_model(authenticated_client, "Part At Root")

    await authenticated_client.patch(
        f"/api/models/{model_in_project['slug']}",
        json={"project_id": proj_id},
    )

    # 1. project=0 returns ONLY root models (project_id is None)
    root_resp = await authenticated_client.get("/api/models?project=0")
    assert root_resp.status_code == 200
    root_names = [m["name"] for m in root_resp.json()["items"]]
    assert "Part At Root" in root_names
    assert "Part In Folder" not in root_names

    # 2. project=proj_id returns ONLY project models
    proj_resp = await authenticated_client.get(f"/api/models?project={proj_id}")
    assert proj_resp.status_code == 200
    proj_names = [m["name"] for m in proj_resp.json()["items"]]
    assert "Part In Folder" in proj_names
    assert "Part At Root" not in proj_names

    # 3. Search query without project filter searches across all models
    search_resp = await authenticated_client.get("/api/models?q=Part")
    assert search_resp.status_code == 200
    all_names = [m["name"] for m in search_resp.json()["items"]]
    assert "Part In Folder" in all_names
    assert "Part At Root" in all_names


async def test_subprojects_and_icons(authenticated_client: httpx.AsyncClient) -> None:
    # 1. Create parent project with icon
    parent = (
        await authenticated_client.post(
            "/api/projects",
            json={"name": "Printer Build", "color": "blue", "icon": "bot"},
        )
    ).json()
    assert parent["icon"] == "bot"
    assert parent["parent_id"] is None

    # 2. Create subproject referencing parent_id
    child = (
        await authenticated_client.post(
            "/api/projects",
            json={"name": "Z Axis", "color": "indigo", "icon": "cog", "parent_id": parent["id"]},
        )
    ).json()
    assert child["icon"] == "cog"
    assert child["parent_id"] == parent["id"]

    # 3. Prevent self-parenting cycle
    self_cycle_resp = await authenticated_client.patch(
        f"/api/projects/{child['id']}",
        json={"parent_id": child["id"]},
    )
    assert self_cycle_resp.status_code == 400

    # 4. Download project zip
    zip_resp = await authenticated_client.get(f"/api/projects/{parent['id']}/zip")
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"


async def test_project_parent_cycle_rejected(authenticated_client: httpx.AsyncClient) -> None:
    a = (await authenticated_client.post("/api/projects", json={"name": "Cycle A"})).json()
    b = (
        await authenticated_client.post(
            "/api/projects", json={"name": "Cycle B", "parent_id": a["id"]}
        )
    ).json()
    resp = await authenticated_client.patch(f"/api/projects/{a['id']}", json={"parent_id": b["id"]})
    assert resp.status_code == 400
