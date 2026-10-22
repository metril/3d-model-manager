"""``GET /api/files/{id}/download`` (SPEC "API surface", Task 6 interface
decisions): streamed download, correct headers, and the "still processing"
409 for files not yet on backend storage.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.api import files as files_api
from app.config import get_settings
from app.main import create_app
from app.models import Blob, File, FileLocation, Model, Revision
from app.models.enums import BlobFormat, BlobKind
from app.services import signed_urls
from app.services import storage_backends as sb
from app.storage.config import LocalConfig
from app.storage.local import LocalStorageBackend
from tests.corpus import CorpusPaths

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


def _anon_client() -> httpx.AsyncClient:
    """A fresh, cookie-less ASGI client against the same app/DB -- distinct
    from ``authenticated_client``'s underlying ``client``, which already
    carries a session cookie once logged in, so it can't be reused to prove
    an endpoint works with NO cookie."""
    transport = httpx.ASGITransport(app=create_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_download_roundtrip_bytes_identical(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Download Target")
    revision_id = created["current_revision"]["id"]
    content = b"downloadable-bytes" * 500

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=content,
    )
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(f"/api/files/{file_id}/download")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-length"] == str(len(content))
    assert 'filename="part.stl"' in response.headers["content-disposition"]


async def test_download_preserves_nested_rel_path_basename(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Nested Download Target")
    revision_id = created["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={
            "model_id": created["id"],
            "revision_id": revision_id,
            "rel_path": "sub/dir/thing.stl",
        },
        content=b"nested-bytes",
    )
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(f"/api/files/{file_id}/download")

    assert response.status_code == 200
    assert 'filename="thing.stl"' in response.headers["content-disposition"]


async def test_download_unstored_file_is_409(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    model = Model(slug="unstored-target", name="Unstored Target")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, name="initial", dir_name="rev-001_initial")
    db_session.add(revision)
    await db_session.flush()
    model.current_revision_id = revision.id

    blob = Blob(hash="a" * 64, size=10, kind=BlobKind.MESH, format=BlobFormat.STL)
    db_session.add(blob)
    await db_session.flush()

    file = File(
        revision_id=revision.id,
        blob_hash=blob.hash,
        rel_path="not-yet-there.stl",
        storage_path=f"{model.slug}/{revision.dir_name}/not-yet-there.stl",
        verified_at=None,
    )
    db_session.add(file)
    await db_session.commit()
    await db_session.refresh(file)

    response = await authenticated_client.get(f"/api/files/{file.id}/download")

    assert response.status_code == 409


async def test_download_unknown_file_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get("/api/files/999999/download")

    assert response.status_code == 404


async def test_download_missing_backend_object_is_404(
    authenticated_client: httpx.AsyncClient,
    db_session,
    backend: LocalStorageBackend,
    seed_file,
) -> None:
    """``verified_at`` says the backend write succeeded, but if the object
    is later removed out-of-band (manual disk edit, scanner cleanup, ...),
    ``backend.read`` raises ``StorageKeyNotFound`` -- that must surface as
    404, not an unhandled 500 (Task 6 review finding).
    """
    created = await _create_model(authenticated_client, "Missing Object Target")
    revision_id = created["current_revision"]["id"]
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, revision_id)

    file = await seed_file(model, revision, "gone.stl", b"will-be-deleted")
    backend.delete(file.storage_path)

    response = await authenticated_client.get(f"/api/files/{file.id}/download")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Workstream C task C2: per-file backend resolution (reads) + default-backend
# write bookkeeping.
# ---------------------------------------------------------------------------


async def test_download_reads_from_the_files_own_non_default_backend(
    authenticated_client: httpx.AsyncClient,
    db_session,
    tmp_path,
) -> None:
    """A ``File`` whose ``backend_id`` points at a NON-default backend is
    read from THAT backend, not the API's shared default -- put bytes on
    backend B only, set the file's ``backend_id=B``, and confirm the
    download streams B's bytes even though B is never the default.
    """
    settings = get_settings()
    default_root = tmp_path / "default-root"
    other_root = tmp_path / "other-root"
    await sb.create_backend(
        db_session, settings, "Default", LocalConfig(root=str(default_root)), is_default=True
    )
    other = await sb.create_backend(
        db_session, settings, "Other", LocalConfig(root=str(other_root))
    )

    other_backend = LocalStorageBackend(other_root)
    content = b"bytes-that-live-only-on-the-other-backend"

    model = Model(slug="cross-backend-target", name="Cross Backend Target")
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, name="initial", dir_name="rev-001_initial")
    db_session.add(revision)
    await db_session.flush()
    model.current_revision_id = revision.id

    blob = Blob(hash="b" * 64, size=len(content), kind=BlobKind.MESH, format=BlobFormat.STL)
    db_session.add(blob)
    await db_session.flush()

    storage_path = f"{model.slug}/{revision.dir_name}/part.stl"
    other_backend.write(storage_path, [content])
    # Deliberately nothing written to the default root -- proves the read
    # can't be accidentally satisfied by the default backend instead.
    assert not (default_root / storage_path).exists()

    file = File(
        revision_id=revision.id,
        blob_hash=blob.hash,
        rel_path="part.stl",
        storage_path=storage_path,
        verified_at=datetime.now(UTC),
        backend_id=other.id,
    )
    db_session.add(file)
    await db_session.commit()
    await db_session.refresh(file)

    response = await authenticated_client.get(f"/api/files/{file.id}/download")

    assert response.status_code == 200
    assert response.content == content


async def test_download_member_gcode_extracts_embedded_plate(
    authenticated_client: httpx.AsyncClient,
    corpus: CorpusPaths,
) -> None:
    """``?member=gcode`` (R10-B) on a ``.gcode.3mf`` streams the embedded
    plate 1 ``.gcode`` (see ``corpus.bambu_gcode``), not the raw zip.
    """
    created = await _create_model(authenticated_client, "Gcode Member Target")
    revision_id = created["current_revision"]["id"]
    content = corpus.sliced_gcode_3mf.read_bytes()

    upload = await authenticated_client.put(
        "/api/uploads",
        params={
            "model_id": created["id"],
            "revision_id": revision_id,
            "rel_path": "print.gcode.3mf",
        },
        content=content,
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(
        f"/api/files/{file_id}/download", params={"member": "gcode"}
    )

    assert response.status_code == 200
    assert response.content == corpus.bambu_gcode.read_bytes()


async def test_download_member_gcode_streams_without_buffering_whole_member(
    corpus: CorpusPaths,
    backend: LocalStorageBackend,
) -> None:
    """Review finding 1: ``?member=gcode`` must never ``.read()`` the whole
    (decompressed) member into memory. Drives the module's own streaming
    helpers directly -- ``_spool_to_temp_file`` (backend has no seekable
    handle, so it spools to a temp file), ``_resolve_gcode_member`` (finds
    the member + its uncompressed size without reading it), and
    ``_stream_gcode_member`` (yields fixed-size chunks) -- and proves: (1)
    the yielded chunks are all <= the fixed chunk size (never one giant
    chunk), (2) their total equals the resolved ``Content-Length`` exactly,
    and (3) the spooled temp file is removed once the stream is consumed.
    """
    storage_path = "gcode-member-stream/print.gcode.3mf"
    backend.write(storage_path, [corpus.sliced_gcode_3mf.read_bytes()])

    tmp_path = files_api._spool_to_temp_file(backend, storage_path)
    assert tmp_path.exists()

    member, file_size = files_api._resolve_gcode_member(tmp_path, plate=None)
    assert file_size == len(corpus.bambu_gcode.read_bytes())

    chunks = list(files_api._stream_gcode_member(tmp_path, member))

    assert all(len(c) <= files_api._GCODE_STREAM_CHUNK_BYTES for c in chunks)
    assert b"".join(chunks) == corpus.bambu_gcode.read_bytes()
    assert sum(len(c) for c in chunks) == file_size
    # The generator's `finally` removed the spooled temp copy once fully
    # consumed -- no leaked temp files per preview request.
    assert not tmp_path.exists()


async def test_download_member_gcode_content_length_matches_body(
    authenticated_client: httpx.AsyncClient,
    corpus: CorpusPaths,
) -> None:
    created = await _create_model(authenticated_client, "Gcode Content Length Target")
    revision_id = created["current_revision"]["id"]
    content = corpus.sliced_gcode_3mf.read_bytes()

    upload = await authenticated_client.put(
        "/api/uploads",
        params={
            "model_id": created["id"],
            "revision_id": revision_id,
            "rel_path": "print.gcode.3mf",
        },
        content=content,
    )
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(
        f"/api/files/{file_id}/download", params={"member": "gcode"}
    )

    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["content-type"].split(";")[0] == "text/x.gcode"


async def test_download_member_gcode_rejects_non_sliced_format(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Non Sliced Target")
    revision_id = created["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=b"not-gcode",
    )
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(
        f"/api/files/{file_id}/download", params={"member": "gcode"}
    )

    assert response.status_code == 400


async def test_download_content_type_by_extension(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """Review finding 4: the raw download's Content-Type is derived from
    the file's own extension, not a blanket octet-stream -- desktop
    slicers refuse an open whose Content-Type they don't recognize.
    """
    cases = [
        ("part.stl", "model/stl"),
        ("part.3mf", "model/3mf"),
        ("part.step", "model/step"),
        ("part.stp", "model/step"),
        ("part.obj", "model/obj"),
        ("part.gcode", "text/x.gcode"),
        ("part.unknownext", "application/octet-stream"),
    ]
    created = await _create_model(authenticated_client, "Content Type Target")
    revision_id = created["current_revision"]["id"]

    for rel_path, expected_media_type in cases:
        upload = await authenticated_client.put(
            "/api/uploads",
            params={"model_id": created["id"], "revision_id": revision_id, "rel_path": rel_path},
            content=b"bytes",
        )
        assert upload.status_code == 201, upload.text
        file_id = upload.json()["file_id"]

        response = await authenticated_client.get(f"/api/files/{file_id}/download")

        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == expected_media_type, rel_path


async def test_download_filename_path_requires_matching_name(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """Review finding 4: ``GET .../download/{filename}`` 404s if
    ``{filename}`` doesn't equal the file's own stored name."""
    created = await _create_model(authenticated_client, "Filename Path Target")
    revision_id = created["current_revision"]["id"]
    content = b"filename-path-bytes"

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=content,
    )
    file_id = upload.json()["file_id"]

    ok = await authenticated_client.get(f"/api/files/{file_id}/download/part.stl")
    assert ok.status_code == 200
    assert ok.content == content
    assert ok.headers["content-type"].split(";")[0] == "model/stl"

    wrong = await authenticated_client.get(f"/api/files/{file_id}/download/wrong.stl")
    assert wrong.status_code == 404


async def test_slicer_link_url_includes_filename(
    authenticated_client: httpx.AsyncClient,
    seed_file,
    db_session,
) -> None:
    """Review finding 4: the minted URL carries the file's own extension
    in its path so a slicer opening the deep link can tell what it is."""
    created = await _create_model(authenticated_client, "Slicer Filename Target")
    model = await db_session.get(Model, created["id"])
    revision_id = created["current_revision"]["id"]
    revision = await db_session.get(Revision, revision_id)
    file = await seed_file(model, revision, "sliced.3mf", b"slicer-bytes")

    link = await authenticated_client.post(f"/api/files/{file.id}/slicer-link")
    assert link.status_code == 200, link.text
    url = link.json()["url"]
    assert f"/api/files/{file.id}/download/sliced.3mf?token=" in url

    path_and_query = url.split("/api", 1)[1]
    async with _anon_client() as anon:
        response = await anon.get(f"/api{path_and_query}")

    assert response.status_code == 200
    assert response.content == b"slicer-bytes"
    assert response.headers["content-type"].split(";")[0] == "model/3mf"


async def test_upload_write_records_default_backend_and_file_location(
    authenticated_client: httpx.AsyncClient,
    db_session,
) -> None:
    """A normal upload's ``store_to_backend`` write lands on the DEFAULT
    backend (self-healed if ``storage_backends`` was empty), stamps
    ``files.backend_id``, and records a ``file_locations`` row.
    """
    created = await _create_model(authenticated_client, "Write Location Target")
    revision_id = created["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=b"tracked-bytes",
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["file_id"]

    default_row = await sb.get_default_backend(db_session)

    file = await db_session.get(File, file_id)
    assert file.backend_id == default_row.id
    assert file.verified_at is not None

    location = await db_session.get(FileLocation, (file_id, default_row.id))
    assert location is not None
    assert location.verified_at is not None


# ---------------------------------------------------------------------------
# R10-C: signed download tokens for desktop slicer deep links.
# ---------------------------------------------------------------------------


async def test_slicer_link_token_allows_download_without_cookie(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Slicer Link Target")
    revision_id = created["current_revision"]["id"]
    content = b"slicer-deep-link-bytes"

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=content,
    )
    file_id = upload.json()["file_id"]

    link = await authenticated_client.post(f"/api/files/{file_id}/slicer-link")
    assert link.status_code == 200, link.text
    body = link.json()
    assert "url" in body and "expires_at" in body
    assert f"/api/files/{file_id}/download" in body["url"]
    assert "token=" in body["url"]

    path_and_query = body["url"].split("/api", 1)[1]
    async with _anon_client() as anon:
        response = await anon.get(f"/api{path_and_query}")

    assert response.status_code == 200
    assert response.content == content


async def test_slicer_link_ignores_forwarded_headers(
    authenticated_client: httpx.AsyncClient,
    seed_file,
    db_session,
) -> None:
    """Review finding 3: a client-supplied ``X-Forwarded-Host``/
    ``X-Forwarded-Proto`` must NOT influence the minted URL's origin --
    only ``request.base_url`` (or ``TDMM_PUBLIC_URL`` if configured, see
    the test below) may.
    """
    created = await _create_model(authenticated_client, "Forwarded Header Target")
    model = await db_session.get(Model, created["id"])
    revision_id = created["current_revision"]["id"]
    revision = await db_session.get(Revision, revision_id)
    file = await seed_file(model, revision, "forwarded.stl", b"bytes")

    response = await authenticated_client.post(
        f"/api/files/{file.id}/slicer-link",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "attacker.example.com",
        },
    )

    assert response.status_code == 200, response.text
    url = response.json()["url"]
    assert "attacker.example.com" not in url
    assert url.startswith("http://test/")


async def test_slicer_link_uses_configured_public_url(
    authenticated_client: httpx.AsyncClient,
    seed_file,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``TDMM_PUBLIC_URL`` is set, it wins over ``request.base_url``
    (and is what an operator behind a reverse proxy/CDN needs)."""
    created = await _create_model(authenticated_client, "Public URL Target")
    model = await db_session.get(Model, created["id"])
    revision_id = created["current_revision"]["id"]
    revision = await db_session.get(Revision, revision_id)
    file = await seed_file(model, revision, "public.stl", b"bytes")

    monkeypatch.setenv("TDMM_PUBLIC_URL", "https://models.example.com")
    get_settings.cache_clear()
    try:
        response = await authenticated_client.post(f"/api/files/{file.id}/slicer-link")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200, response.text
    url = response.json()["url"]
    assert url.startswith(f"https://models.example.com/api/files/{file.id}/download")


async def test_slicer_link_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/files/1/slicer-link")

    assert response.status_code == 401


async def test_download_without_token_still_requires_cookie(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/files/1/download")

    assert response.status_code == 401


async def test_download_rejects_expired_token(
    authenticated_client: httpx.AsyncClient,
    seed_file,
    db_session,
) -> None:
    created = await _create_model(authenticated_client, "Expired Token Target")
    model = await db_session.get(Model, created["id"])
    revision_id = created["current_revision"]["id"]
    revision = await db_session.get(Revision, revision_id)
    file = await seed_file(model, revision, "expiring.stl", b"bytes")

    token = signed_urls.sign_file_download(get_settings(), file.id, ttl_s=-1)

    async with _anon_client() as anon:
        response = await anon.get(f"/api/files/{file.id}/download", params={"token": token})

    assert response.status_code == 401


async def test_download_rejects_tampered_token(
    authenticated_client: httpx.AsyncClient,
    seed_file,
    db_session,
) -> None:
    created = await _create_model(authenticated_client, "Tampered Token Target")
    model = await db_session.get(Model, created["id"])
    revision_id = created["current_revision"]["id"]
    revision = await db_session.get(Revision, revision_id)
    file = await seed_file(model, revision, "tampered.stl", b"bytes")

    token = signed_urls.sign_file_download(get_settings(), file.id)
    mid = len(token) - len(token.rpartition(".")[2]) // 2  # a middle sig char, not padding bits
    tampered = token[:mid] + ("A" if token[mid] != "A" else "B") + token[mid + 1 :]

    async with _anon_client() as anon:
        response = await anon.get(f"/api/files/{file.id}/download", params={"token": tampered})

    assert response.status_code == 401


async def test_download_token_for_one_file_does_not_open_another(
    authenticated_client: httpx.AsyncClient,
    seed_file,
    db_session,
) -> None:
    created = await _create_model(authenticated_client, "Cross File Token Target")
    model = await db_session.get(Model, created["id"])
    revision_id = created["current_revision"]["id"]
    revision = await db_session.get(Revision, revision_id)
    file_a = await seed_file(model, revision, "a.stl", b"aaa")
    file_b = await seed_file(model, revision, "b.stl", b"bbb")

    token_for_a = signed_urls.sign_file_download(get_settings(), file_a.id)

    async with _anon_client() as anon:
        response = await anon.get(f"/api/files/{file_b.id}/download", params={"token": token_for_a})

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# R13c: doc preview (`?inline=1`)
# ---------------------------------------------------------------------------


async def test_download_inline_pdf_sets_inline_disposition(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Inline PDF Target")
    revision_id = created["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "manual.pdf"},
        content=b"%PDF-1.4 fake pdf bytes",
    )
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(
        f"/api/files/{file_id}/download", params={"inline": "1"}
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline;")
    assert 'filename="manual.pdf"' in response.headers["content-disposition"]
    assert response.headers["content-type"] == "application/pdf"


async def test_download_without_inline_param_stays_attachment_for_pdf(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Attachment PDF Target")
    revision_id = created["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "manual.pdf"},
        content=b"%PDF-1.4 fake pdf bytes",
    )
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(f"/api/files/{file_id}/download")

    assert response.headers["content-disposition"].startswith("attachment;")


async def test_download_inline_docx_still_downloads(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """``.docx`` has no reliable in-browser renderer, so `?inline=1` is a
    no-op for it -- it always downloads."""
    created = await _create_model(authenticated_client, "Inline Docx Target")
    revision_id = created["current_revision"]["id"]

    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "bom.docx"},
        content=b"fake docx bytes",
    )
    file_id = upload.json()["file_id"]

    response = await authenticated_client.get(
        f"/api/files/{file_id}/download", params={"inline": "1"}
    )

    assert response.headers["content-disposition"].startswith("attachment;")
