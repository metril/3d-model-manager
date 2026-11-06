"""``GET/PUT /api/settings/storage`` + ``POST /api/settings/storage/test`` +
``POST /api/settings/storage/migrate`` (Task 6 brief).

The migration mechanics themselves (copy+verify+cutover) are covered end to
end in ``tests/test_migrate_task.py`` -- this module only exercises the API
surface: config read/write with secret redaction, the connection-test probe
(success and soft-failure), and that ``POST /migrate`` dispatches a tracked
job.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.services.storage_backends import get_default_backend_row

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def test_get_default_storage_settings_is_local(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.get("/api/settings/storage")

    assert response.status_code == 200
    assert response.json() == {"backend": "local", "config": {"backend": "local", "root": ""}}


async def test_put_s3_config_then_get_redacts_secret(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    payload = {
        "backend": "s3",
        "config": {
            "bucket": "my-bucket",
            "access_key": "AKIAEXAMPLE",
            "secret_key": "super-secret-value",
            "endpoint_url": "http://127.0.0.1:1",
            "prefix": "lib",
            "addressing": "path",
        },
    }

    put_response = await authenticated_client.put("/api/settings/storage", json=payload)

    assert put_response.status_code == 200, put_response.text
    put_body = put_response.json()
    assert put_body["backend"] == "s3"
    assert put_body["config"]["secret_key"] == "***"
    assert put_body["config"]["access_key"] == "AKIAEXAMPLE"

    # M6 A1 + Workstream C: PUT now writes the DEFAULT storage_backends row
    # (not the legacy settings["storage"] row); the secret must be
    # Fernet-encrypted at rest there, not stored plaintext.
    default_row = await get_default_backend_row(db_session)
    assert default_row is not None
    assert default_row.config["secret_key"] != "super-secret-value"

    get_response = await authenticated_client.get("/api/settings/storage")

    assert get_response.status_code == 200
    get_body = get_response.json()
    assert get_body["backend"] == "s3"
    assert get_body["config"]["secret_key"] == "***"
    assert get_body["config"]["bucket"] == "my-bucket"
    # The raw secret must never appear anywhere in the response body.
    assert "super-secret-value" not in get_response.text


async def test_put_rejects_invalid_config(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.put(
        "/api/settings/storage", json={"backend": "smb", "config": {"host": "h"}}
    )

    assert response.status_code == 422


async def test_connection_test_against_local_candidate_ok(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.post(
        "/api/settings/storage/test", json={"backend": "local", "config": {}}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["detail"]
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0


async def test_connection_test_against_dead_s3_endpoint_fails_soft(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.post(
        "/api/settings/storage/test",
        json={
            "backend": "s3",
            "config": {
                "bucket": "nope",
                "access_key": "x",
                "secret_key": "y",
                "endpoint_url": "http://127.0.0.1:1",
                "addressing": "path",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["detail"]


async def test_connection_test_never_persists_the_candidate(
    authenticated_client: httpx.AsyncClient,
) -> None:
    await authenticated_client.post(
        "/api/settings/storage/test",
        json={
            "backend": "s3",
            "config": {
                "bucket": "nope",
                "access_key": "x",
                "secret_key": "y",
                "endpoint_url": "http://127.0.0.1:1",
                "addressing": "path",
            },
        },
    )

    get_response = await authenticated_client.get("/api/settings/storage")

    assert get_response.json()["backend"] == "local"


async def test_migrate_dispatches_tracked_job(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/settings/storage/migrate", json={"backend": "local", "config": {}}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "migrate_storage"
    assert body["id"]
    assert body["state"] in {"queued", "running", "done", "failed"}


# ---------------------------------------------------------------------------
# Secret-merge behavior (gap surfaced during Task 7): GET always redacts a
# set secret to "***", so the frontend never has the real value to send
# back. PUT/test/migrate must transparently substitute the stored secret
# when the incoming one is blank/absent/the redaction sentinel, for the
# same backend type -- without ever leaking the real value in a response.
# ---------------------------------------------------------------------------


async def test_put_omitted_smb_password_keeps_stored_secret(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    from app.services.storage_config import get_active_config

    seed_payload = {
        "backend": "smb",
        "config": {
            "host": "fileserver.local",
            "share": "models",
            "username": "svc",
            "password": "hunter2",
        },
    }
    seed_response = await authenticated_client.put("/api/settings/storage", json=seed_payload)
    assert seed_response.status_code == 200, seed_response.text

    update_payload = {
        "backend": "smb",
        "config": {
            "host": "fileserver.local",
            "root": "sub",
            "share": "models",
            "username": "svc",
            "password": "",
        },
    }
    update_response = await authenticated_client.put("/api/settings/storage", json=update_payload)

    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["config"]["password"] == "***"
    # The raw secret must never appear in a response body.
    assert "hunter2" not in update_response.text

    stored = await get_active_config(db_session, get_settings())
    assert stored.root == "sub"
    assert stored.password.get_secret_value() == "hunter2"


async def test_put_redacted_sentinel_smb_password_keeps_stored_secret(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    from app.services.storage_config import get_active_config

    seed_payload = {
        "backend": "smb",
        "config": {
            "host": "fileserver.local",
            "share": "models",
            "username": "svc",
            "password": "hunter2",
        },
    }
    await authenticated_client.put("/api/settings/storage", json=seed_payload)

    update_payload = {
        "backend": "smb",
        "config": {
            "host": "fileserver.local",
            "root": "sub2",
            "share": "models",
            "username": "svc",
            "password": "***",
        },
    }
    update_response = await authenticated_client.put("/api/settings/storage", json=update_payload)

    assert update_response.status_code == 200, update_response.text
    stored = await get_active_config(db_session, get_settings())
    assert stored.root == "sub2"
    assert stored.password.get_secret_value() == "hunter2"


async def test_put_omitted_s3_secret_key_keeps_stored_secret(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    from app.services.storage_config import get_active_config

    seed_payload = {
        "backend": "s3",
        "config": {
            "bucket": "my-bucket",
            "access_key": "AKIAEXAMPLE",
            "secret_key": "super-secret-value",
            "endpoint_url": "http://127.0.0.1:1",
            "addressing": "path",
        },
    }
    seed_response = await authenticated_client.put("/api/settings/storage", json=seed_payload)
    assert seed_response.status_code == 200, seed_response.text

    update_payload = {
        "backend": "s3",
        "config": {
            "bucket": "my-bucket",
            "prefix": "renamed-prefix",
            "access_key": "AKIAEXAMPLE",
            "secret_key": "",
            "endpoint_url": "http://127.0.0.1:1",
            "addressing": "path",
        },
    }
    update_response = await authenticated_client.put("/api/settings/storage", json=update_payload)

    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["config"]["secret_key"] == "***"
    assert "super-secret-value" not in update_response.text

    stored = await get_active_config(db_session, get_settings())
    assert stored.prefix == "renamed-prefix"
    assert stored.secret_key.get_secret_value() == "super-secret-value"


async def test_put_new_secret_value_is_not_clobbered_by_merge(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    from app.services.storage_config import get_active_config

    seed_payload = {
        "backend": "smb",
        "config": {
            "host": "fileserver.local",
            "share": "models",
            "username": "svc",
            "password": "hunter2",
        },
    }
    await authenticated_client.put("/api/settings/storage", json=seed_payload)

    update_payload = {
        "backend": "smb",
        "config": {
            "host": "fileserver.local",
            "share": "models",
            "username": "svc",
            "password": "brand-new-secret",
        },
    }
    update_response = await authenticated_client.put("/api/settings/storage", json=update_payload)

    assert update_response.status_code == 200, update_response.text
    stored = await get_active_config(db_session, get_settings())
    assert stored.password.get_secret_value() == "brand-new-secret"


async def test_put_blank_secret_with_no_stored_config_of_that_type_still_422s(
    authenticated_client: httpx.AsyncClient,
) -> None:
    # Active config is the default LocalConfig -- there is nothing of the
    # same backend type to merge a secret in from, so a blank/absent secret
    # must still fail validation.
    response = await authenticated_client.put(
        "/api/settings/storage",
        json={
            "backend": "smb",
            "config": {"host": "fileserver.local", "share": "models", "username": "svc"},
        },
    )

    assert response.status_code == 422


async def test_put_redacted_sentinel_with_no_stored_secret_of_that_type_422s(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    """Active config is the default LocalConfig, so a PUT for a *different*
    backend type carrying the literal ``"***"`` redaction sentinel has no
    stored secret to substitute. Regression for the review finding where
    this fell through ``_merge_stored_secrets`` unchanged and got persisted
    verbatim as the "secret" -- it must 422 instead, and never persist.
    """
    from app.services.storage_config import get_active_config

    response = await authenticated_client.put(
        "/api/settings/storage",
        json={
            "backend": "smb",
            "config": {
                "host": "fileserver.local",
                "share": "models",
                "username": "svc",
                "password": "***",
            },
        },
    )

    assert response.status_code == 422, response.text

    stored = await get_active_config(db_session, get_settings())
    assert stored.backend == "local"


async def test_connection_test_reuses_stored_secret_when_omitted(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_payload = {
        "backend": "smb",
        "config": {
            "host": "fileserver.local",
            "share": "models",
            "username": "svc",
            "password": "hunter2",
        },
    }
    seed_response = await authenticated_client.put("/api/settings/storage", json=seed_payload)
    assert seed_response.status_code == 200, seed_response.text

    captured: dict = {}

    class _FakeBackend:
        def __init__(self) -> None:
            self._stash: dict[str, bytes] = {}

        def write(self, key, chunks):
            self._stash[key] = b"".join(chunks)

        def read(self, key):
            yield self._stash[key]

        def delete(self, key):
            self._stash.pop(key, None)

    def _spy_get_backend(settings, config):
        captured["config"] = config
        return _FakeBackend()

    monkeypatch.setattr("app.api.settings.get_backend", _spy_get_backend)

    response = await authenticated_client.post(
        "/api/settings/storage/test",
        json={
            "backend": "smb",
            "config": {"host": "fileserver.local", "share": "models", "username": "svc"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert captured["config"].password.get_secret_value() == "hunter2"


@pytest.mark.asyncio
async def test_merge_stored_secrets_rejects_sentinel_when_stored_secret_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M6-MINOR-1: a ``SecretStr`` is ALWAYS truthy -- even ``SecretStr("")``
    -- so a stored EMPTY secret must NOT masquerade as "present" and let a
    literal ``"***"`` fall through un-rejected. UI/API-unreachable (``_parse``
    blocks storing an empty secret in the first place), so this drives
    ``_merge_stored_secrets`` directly against a hand-built empty-secret
    active config; pre-fix it returned the merged config (no 422), post-fix
    it correctly rejects the sentinel."""
    from fastapi import HTTPException

    from app.api import settings as settings_module
    from app.storage.config import SmbConfig

    empty_secret_cfg = SmbConfig(host="h", share="sh", username="u", password="")

    async def _fake_active(_db, _settings):
        return empty_secret_cfg

    monkeypatch.setattr(settings_module.storage_config, "get_active_config", _fake_active)

    with pytest.raises(HTTPException) as exc:
        await settings_module._merge_stored_secrets(
            None,
            get_settings(),
            "smb",
            {"backend": "smb", "host": "h", "share": "sh", "username": "u", "password": "***"},
        )
    assert exc.value.status_code == 422
