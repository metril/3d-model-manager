"""``POST /api/slicer/intake`` (Round 8 Task 4: Bambu Studio post-processing
intake): raw streamed body -> spool -> resolve/create model -> finalize file
-> ``store_to_backend`` job, mirroring ``app.api.uploads``'s flow but
resolving the ``(model, revision, rel_path)`` target from the filename
alone (``app.services.slicer_intake``). Celery runs in eager mode for the
whole test session (see ``conftest.py::_celery_eager_mode``), so by the time
the POST response comes back the file is already stored and verified.

Also covers ``resolve_and_attach_sync``, the T5 watcher's sync twin, given
an already-staged ``StagedFile`` (mirrors ``tests/test_import_blob_typing
.py``'s ``_stage`` helper / usage pattern).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import blake3
import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.importers.download import StagedFile
from app.models import File, Model
from app.services import api_tokens, layout, slicer_intake, spool
from app.services.storage_config import resolve_backend_sync
from app.storage.local import LocalStorageBackend
from app.tasks import base as tasks_base

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


@pytest.fixture
async def slicer_token(db_session) -> str:
    token, _ = await api_tokens.mint(db_session, label="Bambu post-processing")
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _intake(
    client: httpx.AsyncClient, token: str, *, filename: str, content: bytes
) -> httpx.Response:
    return await client.post(
        "/api/slicer/intake",
        params={"filename": filename},
        content=content,
        headers=_bearer(token),
    )


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


async def test_intake_with_no_authorization_header_is_401(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/slicer/intake", params={"filename": "Benchy.gcode.3mf"}, content=b"bytes"
    )
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_intake_with_invalid_bearer_is_401(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/slicer/intake",
        params={"filename": "Benchy.gcode.3mf"},
        content=b"bytes",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401


async def test_intake_with_session_cookie_only_is_401(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """Scope isolation, same posture as `/ext`: a session cookie (no bearer)
    must not authenticate against the bearer-token-gated slicer plane."""
    r = await authenticated_client.post(
        "/api/slicer/intake", params={"filename": "Benchy.gcode.3mf"}, content=b"bytes"
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# attach / create / replace / traversal
# ---------------------------------------------------------------------------


async def test_intake_attaches_to_existing_model_matched_case_insensitively(
    authenticated_client: httpx.AsyncClient, slicer_token: str
) -> None:
    created = await _create_model(authenticated_client, "Benchy")
    revision_id = created["current_revision"]["id"]
    content = b"fake-sliced-3mf-bytes" * 100

    response = await _intake(
        authenticated_client, slicer_token, filename="Benchy.gcode.3mf", content=content
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["model_id"] == created["id"]
    assert body["model_name"] == "Benchy"
    assert body["action"] == "attached"
    assert body["blob_hash"] == blake3.blake3(content).hexdigest()
    assert body["size"] == len(content)
    uuid.UUID(body["job_id"])  # well-formed uuid

    detail = await authenticated_client.get(f"/api/revisions/{revision_id}")
    file_out = next(f for f in detail.json()["files"] if f["rel_path"] == "Benchy.gcode.3mf")
    assert file_out["blob_hash"] == body["blob_hash"]
    assert file_out["verified_at"] is not None
    assert file_out["kind"] == "sliced"
    assert file_out["format"] == "gcode_3mf"

    jobs_resp = await authenticated_client.get("/api/jobs")
    job = next(j for j in jobs_resp.json() if j["id"] == body["job_id"])
    assert job["state"] == "done"
    assert job["type"] == "store_to_backend"
    assert job["subject_id"] == body["file_id"]


async def test_intake_creates_new_model_when_no_name_matches(
    authenticated_client: httpx.AsyncClient, slicer_token: str, db_session
) -> None:
    content = b"another-fake-sliced-gcode" * 50

    response = await _intake(
        authenticated_client,
        slicer_token,
        filename="New Thing_PLA_1h2m.gcode",
        content=content,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["model_name"] == "New Thing"
    assert body["action"] == "created"

    model = await db_session.get(Model, body["model_id"])
    assert model.name == "New Thing"
    detail = await authenticated_client.get(f"/api/revisions/{model.current_revision_id}")
    files = detail.json()["files"]
    assert [f["rel_path"] for f in files] == ["New Thing_PLA_1h2m.gcode"]


async def test_intake_same_filename_twice_replaces_with_exactly_one_file(
    authenticated_client: httpx.AsyncClient, slicer_token: str, db_session
) -> None:
    first = await _intake(
        authenticated_client, slicer_token, filename="Widget.3mf", content=b"first-bytes" * 10
    )
    assert first.status_code == 201, first.text
    assert first.json()["action"] == "created"
    model_id = first.json()["model_id"]

    second = await _intake(
        authenticated_client, slicer_token, filename="Widget.3mf", content=b"second-bytes" * 10
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["action"] == "replaced"
    assert body["model_id"] == model_id
    assert body["blob_hash"] == blake3.blake3(b"second-bytes" * 10).hexdigest()

    count = await db_session.scalar(
        select(func.count()).select_from(File).where(File.rel_path == "Widget.3mf")
    )
    assert count == 1


async def test_intake_traversal_filename_uses_basename_only(
    authenticated_client: httpx.AsyncClient, slicer_token: str, db_session
) -> None:
    response = await _intake(
        authenticated_client,
        slicer_token,
        filename="../../etc/x.gcode",
        content=b"traversal-bytes" * 5,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["model_name"] == "x"
    assert body["action"] == "created"

    model = await db_session.get(Model, body["model_id"])
    detail = await authenticated_client.get(f"/api/revisions/{model.current_revision_id}")
    files = detail.json()["files"]
    assert [f["rel_path"] for f in files] == ["x.gcode"]  # basename only, no `../../etc/` component


# ---------------------------------------------------------------------------
# rejections
# ---------------------------------------------------------------------------


async def test_intake_unsupported_extension_is_422_naming_the_file(
    client: httpx.AsyncClient, slicer_token: str
) -> None:
    # `.txt` is no longer unsupported (R13c: `BlobKind.DOC`/`BlobFormat.TXT`)
    # -- use a genuinely unrecognized extension instead.
    response = await _intake(client, slicer_token, filename="notes.xyz", content=b"just some text")

    assert response.status_code == 422
    assert "notes.xyz" in response.text


async def test_intake_empty_body_is_400(
    client: httpx.AsyncClient, slicer_token: str, db_session
) -> None:
    response = await _intake(client, slicer_token, filename="Benchy.gcode.3mf", content=b"")

    assert response.status_code == 400
    count = await db_session.scalar(select(func.count()).select_from(Model))
    assert count == 0  # no model created for a rejected empty upload


async def test_resolve_and_attach_model_creation_rolls_back_when_finalize_fails(
    db_session, backend: LocalStorageBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2 fix-review: `create_model`'s commit is deferred into the SAME
    transaction as `finalize_upload`'s -- a failure in `finalize_upload`
    (here simulated directly; in practice the rare concurrent-identical-
    blob 409) must roll back the just-created Model+Revision too, instead
    of leaving an empty, file-less model durably committed."""
    settings = get_settings()

    async def _boom(*_a, **_kw):
        raise RuntimeError("simulated finalize failure")

    monkeypatch.setattr(slicer_intake.library, "finalize_upload", _boom)

    with pytest.raises(RuntimeError, match="simulated finalize failure"):
        await slicer_intake.resolve_and_attach(
            db_session,
            backend,
            settings,
            filename="Orphan Guard_PLA_1h2m.gcode",
            spool_token=uuid.uuid4(),
            spool_path=Path("/does/not/matter"),  # never read before the simulated failure
            blob_hash="a" * 64,
            size=123,
        )
    await db_session.rollback()

    count = await db_session.scalar(select(func.count()).select_from(Model))
    assert count == 0  # the model creation rolled back with the failed finalize


# ---------------------------------------------------------------------------
# sync twin (T5 watcher path)
# ---------------------------------------------------------------------------


def _stage(settings, rel_path: str, content: bytes) -> StagedFile:
    """Test-only twin of ``download.stream_remote_to_spool`` (mirrors
    ``tests/test_import_blob_typing.py``'s ``_stage`` helper): writes
    ``content`` straight to a real spool file and infers ``kind``/``format_``
    from ``rel_path``, exactly like the real watcher flow will."""
    spool.ensure_spool_dir(settings)
    token = uuid.uuid4()
    path = spool.spool_path(settings, token)
    path.write_bytes(content)
    kind, format_ = layout.infer_blob_kind_format(rel_path)
    return StagedFile(
        token=token,
        spool_path=path,
        blob_hash=blake3.blake3(content).hexdigest(),
        size=len(content),
        rel_path=rel_path,
        kind=kind,
        format_=format_,
    )


def test_resolve_and_attach_sync_creates_then_attaches() -> None:
    settings = get_settings()
    content1 = b"sync-create-bytes" * 20
    content2 = b"sync-attach-bytes" * 20

    with tasks_base.sync_session() as s:
        backend = resolve_backend_sync(s, settings)
        first = slicer_intake.resolve_and_attach_sync(
            s,
            backend,
            settings,
            filename="Sync Thing_PLA_3h4m.gcode",
            staged=_stage(settings, "Sync Thing_PLA_3h4m.gcode", content1),
        )
    assert first.action == "created"
    assert first.model_name == "Sync Thing"

    with tasks_base.sync_session() as s:
        backend = resolve_backend_sync(s, settings)
        second = slicer_intake.resolve_and_attach_sync(
            s,
            backend,
            settings,
            filename="Sync Thing_PETG_45m.gcode",
            staged=_stage(settings, "Sync Thing_PETG_45m.gcode", content2),
        )
    assert second.action == "attached"
    assert second.model_id == first.model_id
    assert second.model_name == "Sync Thing"

    with tasks_base.sync_session() as s:
        model = s.get(Model, first.model_id)
        count = s.scalar(
            select(func.count())
            .select_from(File)
            .where(File.revision_id == model.current_revision_id)
        )
        assert count == 2


def test_resolve_and_attach_sync_same_filename_twice_replaces() -> None:
    settings = get_settings()
    filename = "Sync Widget.3mf"
    content1 = b"sync-first-widget-bytes" * 10
    content2 = b"sync-second-widget-bytes" * 10

    with tasks_base.sync_session() as s:
        backend = resolve_backend_sync(s, settings)
        first = slicer_intake.resolve_and_attach_sync(
            s, backend, settings, filename=filename, staged=_stage(settings, filename, content1)
        )
    assert first.action == "created"

    with tasks_base.sync_session() as s:
        backend = resolve_backend_sync(s, settings)
        second = slicer_intake.resolve_and_attach_sync(
            s, backend, settings, filename=filename, staged=_stage(settings, filename, content2)
        )
    assert second.action == "replaced"
    assert second.model_id == first.model_id

    with tasks_base.sync_session() as s:
        model = s.get(Model, first.model_id)
        files = (
            s.execute(select(File).where(File.revision_id == model.current_revision_id))
            .scalars()
            .all()
        )
        assert len(files) == 1
        assert files[0].blob_hash == blake3.blake3(content2).hexdigest()


def test_resolve_and_attach_sync_replace_never_deletes_old_bytes_before_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M3 fix-review: mirrors the async twin's (`finalize_upload`) same-key
    overwrite ordering -- the old File row (and its on-backend bytes) must
    survive a failure in the NEW file's store step. No explicit pre-delete
    of the old bytes; only a same-key overwrite by a SUCCESSFUL new
    ``store_to_backend`` job is ever allowed to touch them."""
    settings = get_settings()
    filename = "Sync Fragile.3mf"
    content1 = b"sync-first-fragile-bytes" * 10

    with tasks_base.sync_session() as s:
        backend = resolve_backend_sync(s, settings)
        first = slicer_intake.resolve_and_attach_sync(
            s, backend, settings, filename=filename, staged=_stage(settings, filename, content1)
        )
    assert first.action == "created"

    with tasks_base.sync_session() as s:
        old_file = s.execute(select(File).where(File.rel_path == filename)).scalar_one()
        old_storage_path = old_file.storage_path
        old_file_id = old_file.id
        backend = resolve_backend_sync(s, settings)
        assert b"".join(backend.read(old_storage_path)) == content1

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated store failure")

    monkeypatch.setattr(slicer_intake.library, "store_imported_file_sync", _boom)

    with tasks_base.sync_session() as s:
        backend = resolve_backend_sync(s, settings)
        with pytest.raises(RuntimeError, match="simulated store failure"):
            slicer_intake.resolve_and_attach_sync(
                s,
                backend,
                settings,
                filename=filename,
                staged=_stage(settings, filename, b"sync-second-fragile-bytes" * 10),
            )
    # `sync_session()`'s `finally: session.close()` rolls back whatever the
    # failed call above only flushed (never committed) -- the old row's
    # `session.delete` included.

    with tasks_base.sync_session() as s:
        files = s.execute(select(File).where(File.rel_path == filename)).scalars().all()
        assert len(files) == 1  # old row survives -- never pre-deleted before the replacement lands
        assert files[0].id == old_file_id
        assert files[0].blob_hash == blake3.blake3(content1).hexdigest()
        backend = resolve_backend_sync(s, settings)
        assert b"".join(backend.read(old_storage_path)) == content1  # bytes never touched either


async def test_intake_over_max_upload_bytes_is_413(
    authenticated_client: httpx.AsyncClient, slicer_token: str, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 4)
    response = await _intake(
        authenticated_client, slicer_token, filename="Benchy.gcode.3mf", content=b"0123456789"
    )
    assert response.status_code == 413
