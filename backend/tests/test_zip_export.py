"""Streaming ZIP export of a model or a followed collection (R11-A plan
item 12): ``GET /api/models/{slug}/zip`` and
``GET /api/collections/{id}/zip``.
"""

from __future__ import annotations

import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import BytesIO

import httpx
import pytest

from app.config import get_settings
from app.models import Revision
from app.models.collections import FollowedCollection
from app.models.enums import BlobFormat, CollectionSyncMode, ImportSite
from app.services import library, zip_export

pytestmark = pytest.mark.usefixtures("library_root")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# GET /api/models/{slug}/zip
# ---------------------------------------------------------------------------


async def test_model_zip_contains_files_and_readme(
    authenticated_client: httpx.AsyncClient, db_session, backend, seed_file
) -> None:
    created = await _create_model(authenticated_client, "Zip Me")
    model = await library.get_model_by_slug(db_session, "zip-me")
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"stl-bytes", blob_format=BlobFormat.STL)
    await seed_file(model, revision, "body.3mf", b"threemf-bytes", blob_format=BlobFormat.THREEMF)

    response = await authenticated_client.get(f"/api/models/{created['slug']}/zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f'filename="{created["slug"]}.zip"' in response.headers["content-disposition"]

    zf = zipfile.ZipFile(BytesIO(response.content))
    names = set(zf.namelist())
    assert names == {"zip-me/README.txt", "zip-me/part.stl", "zip-me/body.3mf"}
    assert zf.read("zip-me/part.stl") == b"stl-bytes"
    assert zf.read("zip-me/body.3mf") == b"threemf-bytes"
    readme = zf.read("zip-me/README.txt").decode()
    assert "Name: Zip Me" in readme
    # 3mf is already compressed -- stored, not deflated.
    assert zf.getinfo("zip-me/body.3mf").compress_type == zipfile.ZIP_STORED
    assert zf.getinfo("zip-me/part.stl").compress_type == zipfile.ZIP_DEFLATED


async def test_model_zip_readme_has_provenance(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    created = await _create_model(authenticated_client, "Provenance Widget")
    model = await library.get_model_by_slug(db_session, created["slug"])
    model.source_url = "https://example.test/thing/1"
    model.source_author = "Some Author"
    model.source_license = "CC-BY-4.0"
    await db_session.commit()
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"bytes")

    response = await authenticated_client.get(f"/api/models/{created['slug']}/zip")

    zf = zipfile.ZipFile(BytesIO(response.content))
    readme = zf.read(f"{created['slug']}/README.txt").decode()
    assert "Source: https://example.test/thing/1" in readme
    assert "Author: Some Author" in readme
    assert "License: CC-BY-4.0" in readme


async def test_model_zip_dedupes_colliding_filenames(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    created = await _create_model(authenticated_client, "Collision Model")
    model = await library.get_model_by_slug(db_session, created["slug"])
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "a/part.stl", b"first")
    await seed_file(model, revision, "b/part.stl", b"second")

    response = await authenticated_client.get(f"/api/models/{created['slug']}/zip")

    zf = zipfile.ZipFile(BytesIO(response.content))
    names = set(zf.namelist())
    slug = created["slug"]
    assert f"{slug}/part.stl" in names
    assert f"{slug}/part (2).stl" in names
    contents = {zf.read(n) for n in (f"{slug}/part.stl", f"{slug}/part (2).stl")}
    assert contents == {b"first", b"second"}


async def test_model_zip_empty_model_is_409(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Empty Model")

    response = await authenticated_client.get(f"/api/models/{created['slug']}/zip")

    assert response.status_code == 409


async def test_model_zip_unknown_slug_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get("/api/models/does-not-exist/zip")
    assert response.status_code == 404


async def test_model_zip_requires_session(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/models/whatever/zip")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/collections/{id}/zip
# ---------------------------------------------------------------------------


async def test_collection_zip_nests_per_model(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    collection = FollowedCollection(
        site=ImportSite.THINGIVERSE,
        list_id="likes",
        kind="likes",
        title="My Faves",
        mode=CollectionSyncMode.REVIEW,
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    created_a = await _create_model(authenticated_client, "Collected A")
    created_b = await _create_model(authenticated_client, "Collected B")
    model_a = await library.get_model_by_slug(db_session, created_a["slug"])
    model_b = await library.get_model_by_slug(db_session, created_b["slug"])
    model_a.source_collection_id = collection.id
    model_b.source_collection_id = collection.id
    await db_session.commit()

    revision_a = await db_session.get(Revision, model_a.current_revision_id)
    revision_b = await db_session.get(Revision, model_b.current_revision_id)
    await seed_file(model_a, revision_a, "a.stl", b"AAA")
    await seed_file(model_b, revision_b, "b.stl", b"BBB")

    response = await authenticated_client.get(f"/api/collections/{collection.id}/zip")

    assert response.status_code == 200
    assert f'filename="{collection.title}.zip"' in response.headers["content-disposition"]
    zf = zipfile.ZipFile(BytesIO(response.content))
    names = set(zf.namelist())
    assert names == {
        f"My Faves/{created_a['slug']}/README.txt",
        f"My Faves/{created_a['slug']}/a.stl",
        f"My Faves/{created_b['slug']}/README.txt",
        f"My Faves/{created_b['slug']}/b.stl",
    }


async def test_collection_zip_sanitizes_traversal_title(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    """A collection title of ``"../evil/"`` must not let entries escape the
    archive root or nest bogusly (review finding 3)."""
    collection = FollowedCollection(
        site=ImportSite.THINGIVERSE,
        list_id="evil-list",
        kind="likes",
        title="../evil/",
        mode=CollectionSyncMode.REVIEW,
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    created = await _create_model(authenticated_client, "Safe Model")
    model = await library.get_model_by_slug(db_session, created["slug"])
    model.source_collection_id = collection.id
    await db_session.commit()
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"data")

    response = await authenticated_client.get(f"/api/collections/{collection.id}/zip")

    assert response.status_code == 200
    zf = zipfile.ZipFile(BytesIO(response.content))
    names = set(zf.namelist())
    assert names == {
        f"evil/{created['slug']}/README.txt",
        f"evil/{created['slug']}/part.stl",
    }
    for name in names:
        assert ".." not in name
        assert not name.startswith("/")


async def test_current_revision_files_fetched_once_per_model(
    authenticated_client: httpx.AsyncClient,
    db_session,
    seed_file,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collection zip must fetch each model's current-revision files
    exactly once, not once for the empty-check and again for the entry loop
    (review finding 6 -- a 200-model export was issuing 400 queries)."""
    collection = FollowedCollection(
        site=ImportSite.THINGIVERSE,
        list_id="count-list",
        kind="likes",
        title="Counted",
        mode=CollectionSyncMode.REVIEW,
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    created_a = await _create_model(authenticated_client, "Count A")
    created_b = await _create_model(authenticated_client, "Count B")
    model_a = await library.get_model_by_slug(db_session, created_a["slug"])
    model_b = await library.get_model_by_slug(db_session, created_b["slug"])
    model_a.source_collection_id = collection.id
    model_b.source_collection_id = collection.id
    await db_session.commit()

    revision_a = await db_session.get(Revision, model_a.current_revision_id)
    revision_b = await db_session.get(Revision, model_b.current_revision_id)
    await seed_file(model_a, revision_a, "a.stl", b"AAA")
    await seed_file(model_b, revision_b, "b.stl", b"BBB")

    calls: list[int] = []
    original = zip_export._current_revision_files

    async def counting(db, model):
        calls.append(model.id)
        return await original(db, model)

    monkeypatch.setattr(zip_export, "_current_revision_files", counting)

    response = await authenticated_client.get(f"/api/collections/{collection.id}/zip")

    assert response.status_code == 200
    assert calls == [model_a.id, model_b.id]


async def test_rest_closes_inner_generator_on_early_disconnect() -> None:
    """When a consumer stops draining ``first_chunk``'s replay generator
    partway through (mirroring a client disconnect), the wrapped generator's
    own ``finally`` must still run -- proving open backend read handles get
    released deterministically instead of waiting on GC (review finding 4).
    """
    closed: list[str] = []

    async def inner() -> AsyncIterator[bytes]:
        try:
            yield b"first"
            yield b"second"
        finally:
            closed.append("inner-closed")

    _chunk, rest = await zip_export.first_chunk(inner())
    agen = rest.__aiter__()
    await agen.__anext__()  # only the first (replayed) chunk

    await agen.aclose()  # simulates the client going away mid-stream

    assert closed == ["inner-closed"]


async def test_collection_zip_skips_models_with_no_files(
    authenticated_client: httpx.AsyncClient, db_session, seed_file
) -> None:
    collection = FollowedCollection(
        site=ImportSite.THINGIVERSE,
        list_id="empty-list",
        kind="likes",
        title="Sparse",
        mode=CollectionSyncMode.REVIEW,
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    created = await _create_model(authenticated_client, "Has Files")
    model = await library.get_model_by_slug(db_session, created["slug"])
    model.source_collection_id = collection.id
    empty_created = await _create_model(authenticated_client, "No Files")
    empty_model = await library.get_model_by_slug(db_session, empty_created["slug"])
    empty_model.source_collection_id = collection.id
    await db_session.commit()

    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "only.stl", b"data")

    response = await authenticated_client.get(f"/api/collections/{collection.id}/zip")

    assert response.status_code == 200
    zf = zipfile.ZipFile(BytesIO(response.content))
    names = set(zf.namelist())
    assert names == {f"Sparse/{created['slug']}/README.txt", f"Sparse/{created['slug']}/only.stl"}


async def test_collection_zip_unknown_id_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get("/api/collections/999999/zip")
    assert response.status_code == 404


async def test_collection_zip_empty_collection_streams_valid_empty_zip(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    collection = FollowedCollection(
        site=ImportSite.THINGIVERSE,
        list_id="nothing",
        kind="likes",
        title="Nothing Here",
        mode=CollectionSyncMode.REVIEW,
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    response = await authenticated_client.get(f"/api/collections/{collection.id}/zip")

    assert response.status_code == 200
    zf = zipfile.ZipFile(BytesIO(response.content))
    assert zf.namelist() == []


# ---------------------------------------------------------------------------
# Streaming proof: a file's storage read isn't even issued until every chunk
# of the previous file has already been yielded out of the generator.
# ---------------------------------------------------------------------------


async def test_iter_model_zip_reads_files_one_at_a_time_not_all_upfront(
    db_session, backend, monkeypatch
) -> None:
    model = await library.create_model(db_session, backend, name="Stream Proof", description=None)
    revision = await db_session.get(Revision, model.current_revision_id)

    from app.models import Blob, File
    from app.models.enums import BlobKind

    read_calls: list[str] = []

    # Seed two files directly (avoid pulling in the full seed_file fixture's
    # own backend.write, since this test replaces `resolve_backend_for_file`
    # with a fake that never touches real storage).
    import blake3

    for rel_path, content in (("a.stl", b"AAAAAAAA"), ("b.stl", b"BBBBBBBB")):
        digest = blake3.blake3(content).hexdigest()
        blob = Blob(hash=digest, size=len(content), kind=BlobKind.MESH, format=BlobFormat.STL)
        db_session.add(blob)
        await db_session.flush()
        db_session.add(
            File(
                revision_id=revision.id,
                blob_hash=digest,
                rel_path=rel_path,
                storage_path=f"{model.slug}/{revision.dir_name}/{rel_path}",
                verified_at=datetime.now(UTC),
            )
        )
    await db_session.commit()

    class _FakeBackend:
        def __init__(self, tag: str) -> None:
            self._tag = tag

        def read(self, storage_path: str):
            read_calls.append(storage_path)

            def _gen():
                yield self._tag.encode()[:4].ljust(4, b"-")
                yield self._tag.encode()[4:8].ljust(4, b"-")

            return _gen()

    async def _fake_resolve(db, settings, file):
        return _FakeBackend(file.rel_path)

    monkeypatch.setattr(zip_export, "resolve_backend_for_file", _fake_resolve)

    settings = get_settings()
    gen = zip_export.iter_model_zip(db_session, settings, model)

    chunks_before_second_read: int | None = None
    total_chunks = 0
    while True:
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            break
        if len(read_calls) >= 2 and chunks_before_second_read is None:
            chunks_before_second_read = total_chunks
        total_chunks += 1

    assert read_calls == [
        f"{model.slug}/{revision.dir_name}/a.stl",
        f"{model.slug}/{revision.dir_name}/b.stl",
    ]
    # Some bytes were already yielded (README + all of a.stl) strictly
    # BEFORE b.stl's read was even issued -- proves true streaming, not
    # "read everything, then zip it".
    assert chunks_before_second_read is not None
    assert chunks_before_second_read > 0


def test_safe_path_segment_falls_back_for_dot_only_results() -> None:
    """A title that sanitizes down to just ``.`` (or ``..``) must not be
    used as a zip path segment -- it falls back to the default instead."""
    assert zip_export._safe_path_segment(".", fallback="collection-1") == "collection-1"


async def test_project_tree_dedupes_colliding_sibling_segments(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """Sibling projects whose names sanitize to the same segment ("ab" and
    "a/b") must get distinct zip prefixes."""
    from app.db import get_sessionmaker
    from app.models import Project

    root = (await authenticated_client.post("/api/projects", json={"name": "Root"})).json()
    for name in ("ab", "a/b"):
        r = await authenticated_client.post(
            "/api/projects", json={"name": name, "parent_id": root["id"]}
        )
        assert r.status_code == 201, r.text

    async with get_sessionmaker()() as db:
        project = await db.get(Project, root["id"])
        assert project is not None
        tree = await zip_export._get_project_tree(db, project)
    prefixes = [prefix for _, prefix in tree]
    assert len(prefixes) == 3
    assert len(set(prefixes)) == 3


async def test_project_tree_dedupe_keeps_dots_in_folder_names(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """Folder segments are not filenames: ``Proj v1.2`` collides as
    ``Proj v1.2 (2)``, never ``Proj v1 (2).2``."""
    from app.db import get_sessionmaker
    from app.models import Project

    root = (await authenticated_client.post("/api/projects", json={"name": "Root"})).json()
    for name in ("Proj v1.2", "Proj v1/.2"):
        r = await authenticated_client.post(
            "/api/projects", json={"name": name, "parent_id": root["id"]}
        )
        assert r.status_code == 201, r.text

    async with get_sessionmaker()() as db:
        project = await db.get(Project, root["id"])
        assert project is not None
        tree = await zip_export._get_project_tree(db, project)
    prefixes = {prefix for _, prefix in tree}
    assert {"Root/Proj v1.2", "Root/Proj v1.2 (2)"} <= prefixes
