"""``GET/POST/PUT/DELETE /api/settings/storage/backends`` + ``.../test`` +
``.../default`` (Workstream C task C3): CRUD over the full multi-backend
``storage_backends`` table, distinct from the legacy single-backend shim
covered by ``tests/test_settings_api.py``.

Every test starts with an EMPTY ``storage_backends`` table (the autouse
``_truncate_all_tables`` fixture wipes the migration's data-seed between
tests, same as ``tests/test_storage_backends.py``) -- each test creates
whatever backend rows it needs from scratch. ``POST`` never makes a backend
the default (that's the dedicated ``.../default`` endpoint); guardrail tests
that need a "default" row call it explicitly.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _create_local(client: httpx.AsyncClient, name: str, root: str = "") -> dict:
    response = await client.post(
        "/api/settings/storage/backends",
        json={"name": name, "config": {"backend": "local", "root": root}},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_local_backend(authenticated_client: httpx.AsyncClient, tmp_path) -> None:
    body = await _create_local(authenticated_client, "Local disk", str(tmp_path / "a"))

    assert body["name"] == "Local disk"
    assert body["scheme"] == "local"
    assert body["is_default"] is False
    assert body["config"] == {"backend": "local", "root": str(tmp_path / "a")}
    assert "id" in body
    assert "created_at" in body


async def test_create_s3_backend_masks_secret(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/settings/storage/backends",
        json={
            "name": "S3 archive",
            "config": {
                "backend": "s3",
                "bucket": "my-bucket",
                "access_key": "AKIAEXAMPLE",
                "secret_key": "super-secret-value",
                "endpoint_url": "http://127.0.0.1:1",
                "addressing": "path",
            },
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scheme"] == "s3"
    assert body["config"]["secret_key"] == "***"
    assert body["config"]["access_key"] == "AKIAEXAMPLE"
    assert "super-secret-value" not in response.text


async def test_create_rejects_the_redaction_sentinel_as_a_new_secret(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.post(
        "/api/settings/storage/backends",
        json={
            "name": "S3",
            "config": {
                "backend": "s3",
                "bucket": "b",
                "access_key": "AK",
                "secret_key": "***",
                "addressing": "path",
            },
        },
    )

    assert response.status_code == 422


async def test_create_rejects_invalid_config(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/settings/storage/backends",
        json={"name": "Bad SMB", "config": {"backend": "smb", "host": "h"}},
    )

    assert response.status_code == 422


async def test_list_backends(authenticated_client: httpx.AsyncClient, tmp_path) -> None:
    a = await _create_local(authenticated_client, "A", str(tmp_path / "a"))
    b = await _create_local(authenticated_client, "B", str(tmp_path / "b"))

    response = await authenticated_client.get("/api/settings/storage/backends")

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert ids == {a["id"], b["id"]}


async def test_update_name_only_leaves_config_untouched(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    created = await _create_local(authenticated_client, "Original", str(tmp_path / "a"))

    response = await authenticated_client.put(
        f"/api/settings/storage/backends/{created['id']}", json={"name": "Renamed"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["config"]["root"] == str(tmp_path / "a")


async def test_update_omitted_s3_secret_key_keeps_stored_secret(
    authenticated_client: httpx.AsyncClient,
) -> None:
    create_response = await authenticated_client.post(
        "/api/settings/storage/backends",
        json={
            "name": "S3",
            "config": {
                "backend": "s3",
                "bucket": "b",
                "access_key": "AK",
                "secret_key": "hunter2",
                "addressing": "path",
            },
        },
    )
    backend_id = create_response.json()["id"]

    response = await authenticated_client.put(
        f"/api/settings/storage/backends/{backend_id}",
        json={
            "config": {
                "backend": "s3",
                "bucket": "b",
                "prefix": "renamed-prefix",
                "access_key": "AK",
                "addressing": "path",
            }
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["config"]["prefix"] == "renamed-prefix"
    assert body["config"]["secret_key"] == "***"


async def test_update_redacted_sentinel_secret_keeps_stored_secret(
    authenticated_client: httpx.AsyncClient,
) -> None:
    create_response = await authenticated_client.post(
        "/api/settings/storage/backends",
        json={
            "name": "S3",
            "config": {
                "backend": "s3",
                "bucket": "b",
                "access_key": "AK",
                "secret_key": "hunter2",
                "addressing": "path",
            },
        },
    )
    backend_id = create_response.json()["id"]

    response = await authenticated_client.put(
        f"/api/settings/storage/backends/{backend_id}",
        json={
            "config": {
                "backend": "s3",
                "bucket": "b",
                "access_key": "AK",
                "secret_key": "***",
                "addressing": "path",
            }
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["config"]["secret_key"] == "***"


async def test_update_sentinel_with_no_stored_secret_of_that_type_is_422(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    created = await _create_local(authenticated_client, "Local", str(tmp_path / "a"))

    response = await authenticated_client.put(
        f"/api/settings/storage/backends/{created['id']}",
        json={
            "config": {
                "backend": "s3",
                "bucket": "b",
                "access_key": "AK",
                "secret_key": "***",
                "addressing": "path",
            }
        },
    )

    assert response.status_code == 422


async def test_update_unknown_backend_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.put(
        "/api/settings/storage/backends/999999", json={"name": "x"}
    )

    assert response.status_code == 404


async def test_set_default_flips_the_flag(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    a = await _create_local(authenticated_client, "A", str(tmp_path / "a"))
    b = await _create_local(authenticated_client, "B", str(tmp_path / "b"))

    response = await authenticated_client.post(f"/api/settings/storage/backends/{b['id']}/default")

    assert response.status_code == 200, response.text
    assert response.json()["is_default"] is True

    list_response = await authenticated_client.get("/api/settings/storage/backends")
    by_id = {row["id"]: row for row in list_response.json()}
    assert by_id[a["id"]]["is_default"] is False
    assert by_id[b["id"]]["is_default"] is True


async def test_delete_refuses_the_last_backend(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    only = await _create_local(authenticated_client, "Only", str(tmp_path / "a"))

    response = await authenticated_client.delete(f"/api/settings/storage/backends/{only['id']}")

    assert response.status_code == 409


async def test_delete_refuses_the_default_backend(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    default = await _create_local(authenticated_client, "Default", str(tmp_path / "a"))
    await _create_local(authenticated_client, "Spare", str(tmp_path / "b"))
    await authenticated_client.post(f"/api/settings/storage/backends/{default['id']}/default")

    response = await authenticated_client.delete(f"/api/settings/storage/backends/{default['id']}")

    assert response.status_code == 409


async def test_delete_succeeds_for_a_spare_backend(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    default = await _create_local(authenticated_client, "Default", str(tmp_path / "a"))
    spare = await _create_local(authenticated_client, "Spare", str(tmp_path / "b"))
    await authenticated_client.post(f"/api/settings/storage/backends/{default['id']}/default")

    response = await authenticated_client.delete(f"/api/settings/storage/backends/{spare['id']}")

    assert response.status_code == 204
    get_response = await authenticated_client.get("/api/settings/storage/backends")
    assert [row["id"] for row in get_response.json()] == [default["id"]]


async def test_connection_test_against_a_local_backend_ok(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    created = await _create_local(authenticated_client, "Local", str(tmp_path / "a"))

    response = await authenticated_client.post(
        f"/api/settings/storage/backends/{created['id']}/test"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert isinstance(body["latency_ms"], int)


async def test_connection_test_unknown_backend_is_404(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.post("/api/settings/storage/backends/999999/test")

    assert response.status_code == 404


# -- "Move all models here" (M8 F: bulk relocate replacing legacy migrate) ----


async def test_migrate_library_to_backend_sets_default_and_runs_a_job(
    authenticated_client: httpx.AsyncClient, tmp_path
) -> None:
    primary = await _create_local(authenticated_client, "Primary", str(tmp_path / "p"))
    await authenticated_client.post(f"/api/settings/storage/backends/{primary['id']}/default")
    spare = await _create_local(authenticated_client, "Spare", str(tmp_path / "s"))

    response = await authenticated_client.post(
        f"/api/settings/storage/backends/{spare['id']}/migrate"
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "relocate_all"

    listing = (await authenticated_client.get("/api/settings/storage/backends")).json()
    by_id = {b["id"]: b for b in listing}
    assert by_id[spare["id"]]["is_default"] is True
    assert by_id[primary["id"]]["is_default"] is False


async def test_migrate_library_to_unknown_backend_is_404(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.post("/api/settings/storage/backends/999999/migrate")

    assert response.status_code == 404
