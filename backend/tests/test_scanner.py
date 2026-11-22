"""``scan_library`` reconciler (SPEC "Rescan/reconcile"; Task 5 brief).

One test per row of the SPEC decision table, driving ``scanner.run_scan``
directly against a real local backend + the real Postgres testcontainer.
``run_scan`` takes a SYNC ``Session`` (worker world, see ``app.tasks.base``)
while the fixtures seed through the ASYNC ``db_session`` -- exactly like
``tests/test_jobs_api.py``'s Celery-task tests, any row touched by both
sides needs an explicit ``await db_session.refresh(...)`` after the sync
side commits, since the two sessions/connections have independent identity
maps.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import blake3
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Blob, File, FileLocation, Job, Model, Revision, ScanRun
from app.models.enums import BlobFormat, BlobKind
from app.services import layout, scanner, storage_backends
from app.storage.base import EntryInfo
from app.storage.config import LocalConfig
from app.storage.errors import StorageKeyNotFound
from app.storage.local import LocalStorageBackend
from app.tasks import base

pytestmark = pytest.mark.usefixtures("library_root", "redis_url")


async def _create_model_and_revision(
    db_session: AsyncSession, slug: str, name: str
) -> tuple[Model, Revision]:
    model = Model(slug=slug, name=name)
    db_session.add(model)
    await db_session.flush()
    revision = Revision(model_id=model.id, number=1, name="initial", dir_name="rev-001_initial")
    db_session.add(revision)
    await db_session.flush()
    model.current_revision_id = revision.id
    await db_session.commit()
    await db_session.refresh(model)
    await db_session.refresh(revision)
    return model, revision


def _create_scan_run() -> int:
    with base.sync_session() as session:
        scan_run = ScanRun(state="running")
        session.add(scan_run)
        session.commit()
        session.refresh(scan_run)
        return scan_run.id


def _run_scan(backend: LocalStorageBackend) -> ScanRun:
    settings = get_settings()
    scan_run_id = _create_scan_run()
    with base.sync_session() as session:
        scanner.run_scan(session, settings, backend, scan_run_id)
    with base.sync_session() as session:
        scan_run = session.get(ScanRun, scan_run_id)
        session.expunge(scan_run)
        return scan_run


async def _sync_mtime(db_session: AsyncSession, backend: LocalStorageBackend, file: File) -> None:
    """Backfill ``file.mtime`` to match what's really on disk, so a
    known/unchanged file gets the cheap size+mtime match instead of always
    falling into the "changed" (rehash) branch on its very first scan.

    Uses the shared ``conftest.seed_file`` fixture, which (like the real
    upload path pre-Task-5) never itself sets ``mtime`` -- only a completed
    ``store_to_backend``/scan does.
    """
    stat = backend.stat(file.storage_path)
    file.mtime = stat.mtime
    await db_session.commit()


# ---------------------------------------------------------------------------
# 1. Known path, unchanged
# ---------------------------------------------------------------------------


async def test_known_unchanged_touches_verified_at(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _create_model_and_revision(db_session, "widget", "Widget")
    file = await seed_file(model, revision, "part.stl", b"unchanged-bytes")
    await _sync_mtime(db_session, backend, file)
    file.verified_at = None
    await db_session.commit()
    original_blob_hash = file.blob_hash

    scan_run = _run_scan(backend)

    await db_session.refresh(file)
    assert file.verified_at is not None
    assert file.blob_hash == original_blob_hash
    assert scan_run.files_hashed == 0
    assert scan_run.files_seen == 1
    assert scan_run.report["verified"] == 1
    assert scan_run.state == "done"
    assert scan_run.finished_at is not None


# ---------------------------------------------------------------------------
# 2. Known path, changed
# ---------------------------------------------------------------------------


async def test_known_changed_content_repoints_blob_and_reports(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _create_model_and_revision(db_session, "widget", "Widget")
    file = await seed_file(model, revision, "part.stl", b"original-bytes")
    await _sync_mtime(db_session, backend, file)
    old_hash = file.blob_hash

    # Out-of-band edit: overwrite the same storage_path with different bytes,
    # simulating a user editing the file directly on disk.
    backend.write(file.storage_path, [b"edited-bytes-totally-different"])

    scan_run = _run_scan(backend)

    await db_session.refresh(file)
    assert file.blob_hash != old_hash
    new_blob = await db_session.get(Blob, file.blob_hash)
    assert new_blob is not None
    assert new_blob.size == len(b"edited-bytes-totally-different")
    assert scan_run.files_hashed == 1
    assert scan_run.relinked == 0
    assert scan_run.adopted == 0
    changed = scan_run.report["changed"]
    assert len(changed) == 1
    assert changed[0]["file_id"] == file.id
    assert changed[0]["old_hash"] == old_hash
    assert changed[0]["new_hash"] == file.blob_hash

    # Best-effort pipeline dispatch happened for the new blob.
    jobs = (
        (
            await db_session.execute(
                select(Job).where(Job.subject_type == "file", Job.subject_id == file.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(j.type == "extract_metadata" for j in jobs)


# ---------------------------------------------------------------------------
# 3. Unknown path, matching hash -> relink (headline test)
# ---------------------------------------------------------------------------


async def test_unknown_path_matching_hash_relinks_moved_file(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    library_root,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    moved_model, moved_revision = await _create_model_and_revision(db_session, "widget", "Widget")
    moved_file = await seed_file(moved_model, moved_revision, "part.stl", b"moved-bytes")
    await _sync_mtime(db_session, backend, moved_file)

    # An entirely separate, untouched model+file elsewhere in the tree --
    # proves the scan only re-hashes the moved file, "not the whole tree".
    stay_model, stay_revision = await _create_model_and_revision(db_session, "gadget", "Gadget")
    stay_file = await seed_file(stay_model, stay_revision, "stay.stl", b"untouched-bytes")
    await _sync_mtime(db_session, backend, stay_file)

    old_path = moved_file.storage_path
    os.rename(library_root / "widget", library_root / "widget-moved")
    new_path = old_path.replace("widget/", "widget-moved/", 1)

    scan_run = _run_scan(backend)

    await db_session.refresh(moved_file)
    await db_session.refresh(stay_file)

    assert moved_file.storage_path == new_path
    assert scan_run.relinked == 1
    assert scan_run.files_hashed == 1  # only the moved file, not stay.stl
    assert scan_run.missing == 0
    relinked = scan_run.report["relinked"]
    assert len(relinked) == 1
    assert relinked[0]["file_id"] == moved_file.id
    assert relinked[0]["from"] == old_path
    assert relinked[0]["to"] == new_path
    assert relinked[0]["hash"] == moved_file.blob_hash

    # The untouched file was cheaply verified, not rehashed or relinked.
    assert stay_file.storage_path == "gadget/rev-001_initial/stay.stl"
    assert scan_run.report["verified"] == 1


# ---------------------------------------------------------------------------
# 3b. Task 5 fix-wave Finding 1 regression: relink must only claim a row
#     from the CONFIRMED-missing set, never a not-yet-walked row whose own
#     on-disk file is still present.
# ---------------------------------------------------------------------------


async def test_unknown_path_relink_only_claims_confirmed_missing_row(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    library_root,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """Two byte-identical files sharing a basename ("part.stl") under two
    different models. Moving only one must relink THAT file and must never
    touch the other, still-present one -- even though, mid-walk, the
    still-present row can look like an equally good relink candidate simply
    because the walk hasn't reached its own (unchanged) path yet.

    The pre-fix single-pass algorithm broke the hash+tail tie by lowest
    ``File.id`` among ALL not-yet-visited rows, not just confirmed-missing
    ones -- so creating the stay-put model ("b") FIRST (giving it the lower
    id) and the moved model ("a") SECOND reliably reproduces the bug: the
    buggy code relinks "b" (still present) to "a"'s moved location, then
    reports "a" (the file that actually moved) as missing.
    """
    stay_model, stay_revision = await _create_model_and_revision(db_session, "b", "B")
    stay_file = await seed_file(stay_model, stay_revision, "part.stl", b"identical-bytes")
    await _sync_mtime(db_session, backend, stay_file)

    moved_model, moved_revision = await _create_model_and_revision(db_session, "a", "A")
    moved_file = await seed_file(moved_model, moved_revision, "part.stl", b"identical-bytes")
    await _sync_mtime(db_session, backend, moved_file)

    old_path = moved_file.storage_path
    os.rename(library_root / "a", library_root / "a-moved")
    new_path = old_path.replace("a/", "a-moved/", 1)

    scan_run = _run_scan(backend)

    await db_session.refresh(moved_file)
    await db_session.refresh(stay_file)

    # The file that actually moved is the one that gets relinked.
    assert moved_file.storage_path == new_path
    assert scan_run.relinked == 1
    relinked = scan_run.report["relinked"]
    assert len(relinked) == 1
    assert relinked[0]["file_id"] == moved_file.id
    assert relinked[0]["from"] == old_path
    assert relinked[0]["to"] == new_path

    # The still-present file is untouched: not relinked away from its own
    # path, not reported missing, not duplicated into a false adopted draft.
    assert stay_file.storage_path == "b/rev-001_initial/part.stl"
    assert scan_run.missing == 0
    assert scan_run.report["missing"] == []
    assert scan_run.adopted == 0


# ---------------------------------------------------------------------------
# 4. Unknown path, unknown hash, doesn't fit any model -> adopt as draft
# ---------------------------------------------------------------------------


async def test_unknown_folder_adopts_as_draft_model_review_me(
    db_session: AsyncSession, backend: LocalStorageBackend
) -> None:
    backend.write("random-drop/thing.stl", [b"dropped-bytes"])

    scan_run = _run_scan(backend)

    model = (
        await db_session.execute(select(Model).where(Model.slug == "random-drop"))
    ).scalar_one_or_none()
    assert model is not None
    assert model.review_state == "adopted"
    assert model.current_revision_id is not None

    revision = await db_session.get(Revision, model.current_revision_id)
    assert revision.number == 1
    assert revision.dir_name == "rev-001_initial"

    file = (
        await db_session.execute(select(File).where(File.revision_id == revision.id))
    ).scalar_one()
    assert file.storage_path == "random-drop/thing.stl"
    assert file.rel_path == "thing.stl"

    sidecar_bytes = b"".join(backend.read("random-drop/.3dmm.json"))
    sidecar = json.loads(sidecar_bytes)
    assert sidecar == {"model_id": model.id, "slug": "random-drop", "name": "random-drop"}

    assert scan_run.adopted == 1
    adopted = scan_run.report["adopted"]
    assert len(adopted) == 1
    assert adopted[0] == {
        "model_id": model.id,
        "slug": "random-drop",
        "revision_id": revision.id,
        "files": ["thing.stl"],
    }


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
async def test_adopt_sidecar_write_failure_does_not_abort_scan(
    db_session: AsyncSession, backend: LocalStorageBackend
) -> None:
    backend.write("ro-drop/thing.stl", [b"dropped-bytes"])
    folder = Path(backend.root) / "ro-drop"
    folder.chmod(0o555)
    try:
        scan_run = _run_scan(backend)
    finally:
        folder.chmod(0o755)

    assert scan_run.state != "failed"
    model = (await db_session.execute(select(Model).where(Model.slug == "ro-drop"))).scalar_one()
    assert model.review_state == "adopted"
    assert scan_run.adopted == 1
    errors = scan_run.report["errors"]
    assert any(e["storage_path"].endswith(".3dmm.json") for e in errors)


# ---------------------------------------------------------------------------
# 4b. C1 regression: TWO distinct new (unknown-fit) top-level folders with
#     unknown-hash files land in the SAME pass-2 chunk. The second folder's
#     draft-model creation autoflushes (`_unique_slug_sync`'s select, plus
#     `_resolve_adopt_target`'s explicit flushes) mid-loop -- that used to
#     insert the FIRST folder's already-``session.add``-ed File row while its
#     Blob still sat only in a deferred `new_blobs` list, never having been
#     added to the session, tripping `fk_files_blob_hash_blobs`.
# ---------------------------------------------------------------------------


async def test_two_new_draft_folders_in_one_chunk_does_not_violate_blob_fk(
    db_session: AsyncSession, backend: LocalStorageBackend
) -> None:
    backend.write("alpha-drop/thing.stl", [b"alpha-bytes"])
    backend.write("beta-drop/thing.stl", [b"beta-bytes"])

    scan_run = _run_scan(backend)

    assert scan_run.state == "done"
    assert scan_run.adopted == 2

    for slug in ("alpha-drop", "beta-drop"):
        model = (
            await db_session.execute(select(Model).where(Model.slug == slug))
        ).scalar_one_or_none()
        assert model is not None
        assert model.review_state == "adopted"
        assert model.current_revision_id is not None


# ---------------------------------------------------------------------------
# 5. Unknown path, unknown hash, fits an existing model+revision -> attach
# ---------------------------------------------------------------------------


async def test_unknown_path_under_existing_model_attaches_to_revision(
    db_session: AsyncSession, backend: LocalStorageBackend
) -> None:
    model, revision = await _create_model_and_revision(db_session, "widget", "Widget")
    backend.write(layout.file_key("widget", "rev-001_initial", "extra.stl"), [b"extra-bytes"])

    scan_run = _run_scan(backend)

    files = (
        (await db_session.execute(select(File).where(File.revision_id == revision.id)))
        .scalars()
        .all()
    )
    assert len(files) == 1
    assert files[0].rel_path == "extra.stl"
    assert files[0].storage_path == "widget/rev-001_initial/extra.stl"

    model_count = (await db_session.execute(select(Model))).scalars().all()
    assert len(model_count) == 1  # no new model created

    assert scan_run.adopted == 1
    assert scan_run.report["adopted"][0]["model_id"] == model.id
    assert scan_run.report["adopted"][0]["slug"] == "widget"
    assert scan_run.report["adopted"][0]["revision_id"] == revision.id


# ---------------------------------------------------------------------------
# 5b. Unknown path, KNOWN hash (duplicate content), fits existing model,
#     no relink candidate -> adopted duplicate (distinct branch from 5).
# ---------------------------------------------------------------------------


async def test_unknown_path_duplicate_hash_adopts_as_new_file(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _create_model_and_revision(db_session, "widget", "Widget")
    original = await seed_file(model, revision, "part.stl", b"shared-content")
    await _sync_mtime(db_session, backend, original)

    # Same bytes, different filename, dropped directly with no File row --
    # not a relink candidate (rel_path tail doesn't match "part.stl").
    backend.write(
        layout.file_key("widget", "rev-001_initial", "duplicate.stl"), [b"shared-content"]
    )

    scan_run = _run_scan(backend)

    blobs = (await db_session.execute(select(Blob))).scalars().all()
    assert len(blobs) == 1  # no new blob -- same content, deduped

    files = (
        (await db_session.execute(select(File).where(File.revision_id == revision.id)))
        .scalars()
        .all()
    )
    assert {f.rel_path for f in files} == {"part.stl", "duplicate.stl"}

    assert scan_run.adopted == 1
    assert scan_run.relinked == 0
    assert scan_run.missing == 0


# ---------------------------------------------------------------------------
# 6. Snapshot path never seen -> missing (row + object untouched, never
#    deleted -- the CRITICAL never-delete invariant).
# ---------------------------------------------------------------------------


async def test_missing_db_path_marked_missing_never_deleted(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    library_root,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _create_model_and_revision(db_session, "widget", "Widget")
    file = await seed_file(model, revision, "part.stl", b"soon-to-vanish")
    await _sync_mtime(db_session, backend, file)
    file_id = file.id
    storage_path = file.storage_path

    # Remove the bytes directly on disk (bypassing the backend entirely) --
    # simulates the user deleting the file out-of-band.
    (library_root / storage_path).unlink()

    scan_run = _run_scan(backend)

    reloaded = await db_session.get(File, file_id)
    assert reloaded is not None  # row NEVER deleted
    assert reloaded.storage_path == storage_path  # untouched
    assert reloaded.blob_hash == file.blob_hash  # untouched

    assert scan_run.missing == 1
    missing = scan_run.report["missing"]
    assert len(missing) == 1
    assert missing[0]["file_id"] == file_id
    assert missing[0]["storage_path"] == storage_path
    assert missing[0]["model_slug"] == "widget"


# ---------------------------------------------------------------------------
# 7. Counters + report persisted on the ScanRun row.
# ---------------------------------------------------------------------------


async def test_scan_run_report_and_counters_persisted(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    library_root,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model, revision = await _create_model_and_revision(db_session, "widget", "Widget")

    verified_file = await seed_file(model, revision, "verified.stl", b"stays-put")
    await _sync_mtime(db_session, backend, verified_file)

    moved_file = await seed_file(model, revision, "moved.stl", b"will-move")
    await _sync_mtime(db_session, backend, moved_file)

    missing_file = await seed_file(model, revision, "gone.stl", b"will-vanish")
    await _sync_mtime(db_session, backend, missing_file)

    # Move moved.stl to an unknown path, preserving its filename (the
    # relink match is by hash + matching `rel_path` tail) -- a relink
    # candidate.
    backend.copy(moved_file.storage_path, "widget/rev-002_elsewhere/moved.stl")
    (library_root / moved_file.storage_path).unlink()

    # Delete gone.stl's bytes (missing candidate).
    (library_root / missing_file.storage_path).unlink()

    # Drop a brand new adopted file.
    backend.write("widget/rev-001_initial/new-drop.stl", [b"brand-new-bytes"])

    scan_run = _run_scan(backend)

    assert scan_run.state == "done"
    assert scan_run.finished_at is not None
    assert scan_run.files_seen == 3  # verified + moved-elsewhere + new-drop
    assert scan_run.relinked == 1
    assert scan_run.adopted == 1
    assert scan_run.missing == 1
    report = scan_run.report
    assert report["verified"] == 1
    assert len(report["relinked"]) == 1
    assert len(report["adopted"]) == 1
    assert len(report["missing"]) == 1
    assert report["changed"] == []


# ---------------------------------------------------------------------------
# Stale sidecar refresh (Global Constraints carried item: the scanner
# refreshes stale sidecars during reconcile).
# ---------------------------------------------------------------------------


async def test_stale_sidecar_refreshed_during_scan(
    db_session: AsyncSession, backend: LocalStorageBackend
) -> None:
    model, _revision = await _create_model_and_revision(db_session, "widget", "New Name")
    # Simulate a sidecar written before a rename (the pre-Task-5 bug this
    # Global Constraints item calls out): stale content still says "Old Name".
    layout.write_sidecar(backend, model.id, "widget", "Old Name")

    _run_scan(backend)

    sidecar_bytes = b"".join(backend.read("widget/.3dmm.json"))
    sidecar = json.loads(sidecar_bytes)
    assert sidecar == {"model_id": model.id, "slug": "widget", "name": "New Name"}


# ---------------------------------------------------------------------------
# Task 5 fix-wave Finding 3: one unreadable file must not abort the scan.
# ---------------------------------------------------------------------------


async def test_unreadable_file_recorded_as_error_and_scan_continues(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file: Callable[..., Awaitable[File]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TOCTOU deletion (or a transient SMB/S3 read error) on one file's
    re-hash must not discard the whole run's reconcile progress -- it's
    recorded in ``report["errors"]`` and the scan finishes ``done``, with
    every other file still reconciled normally.
    """
    model, revision = await _create_model_and_revision(db_session, "widget", "Widget")
    ok_file = await seed_file(model, revision, "ok.stl", b"fine-bytes")
    await _sync_mtime(db_session, backend, ok_file)

    # A brand-new, unknown-path file whose read will fail during the pass-2
    # re-hash -- simulates the file vanishing (or a backend hiccup) between
    # `walk()` discovering the key and the scanner actually reading it.
    broken_key = "widget/rev-001_initial/broken.stl"
    backend.write(broken_key, [b"will-fail-to-read"])

    real_read = backend.read

    def flaky_read(key: str, *args: object, **kwargs: object):
        if key == broken_key:
            raise StorageKeyNotFound(key)
        return real_read(key, *args, **kwargs)

    monkeypatch.setattr(backend, "read", flaky_read)

    scan_run = _run_scan(backend)

    assert scan_run.state == "done"
    assert scan_run.finished_at is not None

    errors = scan_run.report["errors"]
    assert len(errors) == 1
    assert errors[0]["storage_path"] == broken_key
    assert errors[0]["error"]

    # The broken file wasn't counted toward any outcome.
    assert scan_run.adopted == 0
    assert scan_run.relinked == 0
    assert scan_run.missing == 0

    # The healthy file was still reconciled normally.
    await db_session.refresh(ok_file)
    assert ok_file.verified_at is not None
    assert scan_run.report["verified"] == 1


# ---------------------------------------------------------------------------
# Workstream C task C2: multi-backend reconcile (`run_scan_all_backends`,
# the entrypoint `app.tasks.scan.scan_library` actually calls). `run_scan`
# itself (exercised by every test above) keeps its original single-backend
# signature/behavior unchanged for backward compatibility.
# ---------------------------------------------------------------------------


def _run_scan_all_backends() -> ScanRun:
    settings = get_settings()
    scan_run_id = _create_scan_run()
    with base.sync_session() as session:
        scanner.run_scan_all_backends(session, settings, scan_run_id)
    with base.sync_session() as session:
        scan_run = session.get(ScanRun, scan_run_id)
        session.expunge(scan_run)
        return scan_run


async def test_multi_backend_scan_does_not_mark_other_backends_files_missing(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    tmp_path: Path,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """With two backends configured, each gets its OWN reconcile pass scoped
    to its own files (``_reconcile_against_backend``'s ``missing_candidates``
    is built only from that backend's rows) -- a file that lives only on
    backend B must not be reported ``missing`` just because backend A's walk
    (which never even sees B's tree) doesn't turn it up, and vice versa.
    """
    settings = get_settings()
    other_root = tmp_path / "backend-b"
    other_root.mkdir()
    other_backend = LocalStorageBackend(other_root)

    with base.sync_session() as session:
        default_row = storage_backends.create_backend_sync(
            session, settings, "A", LocalConfig(), is_default=True
        )
        other_row = storage_backends.create_backend_sync(
            session, settings, "B", LocalConfig(root=str(other_root))
        )
        default_id, other_id = default_row.id, other_row.id

    # A file on the DEFAULT backend (A) -- bytes written through the shared
    # `backend` fixture (rooted at `library_root`, same as A's config).
    model_a, rev_a = await _create_model_and_revision(db_session, "widget-a", "Widget A")
    file_a = await seed_file(model_a, rev_a, "part.stl", b"backend-a-bytes")
    await _sync_mtime(db_session, backend, file_a)
    file_a.backend_id = default_id
    await db_session.commit()

    # A file that lives ONLY on the OTHER backend (B) -- bytes never written
    # anywhere under `library_root` at all.
    model_b, rev_b = await _create_model_and_revision(db_session, "widget-b", "Widget B")
    content_b = b"backend-b-bytes"
    storage_path_b = f"{model_b.slug}/{rev_b.dir_name}/part.stl"
    other_backend.write(storage_path_b, [content_b])
    blob_b = Blob(
        hash=blake3.blake3(content_b).hexdigest(),
        size=len(content_b),
        kind=BlobKind.MESH,
        format=BlobFormat.STL,
    )
    db_session.add(blob_b)
    await db_session.flush()
    file_b = File(
        revision_id=rev_b.id,
        blob_hash=blob_b.hash,
        rel_path="part.stl",
        storage_path=storage_path_b,
        mtime=other_backend.stat(storage_path_b).mtime,
        verified_at=None,
        backend_id=other_id,
    )
    db_session.add(file_b)
    await db_session.commit()

    scan_run = _run_scan_all_backends()

    assert scan_run.state == "done"
    # Neither file is missing -- each was found on its OWN backend's walk.
    assert scan_run.missing == 0
    assert scan_run.report["missing"] == []
    assert scan_run.files_seen == 2
    assert scan_run.report["verified"] == 2

    await db_session.refresh(file_a)
    await db_session.refresh(file_b)
    assert file_a.verified_at is not None
    assert file_a.backend_id == default_id
    assert file_b.verified_at is not None
    assert file_b.backend_id == other_id


async def test_multi_backend_scan_adopts_orphan_file_onto_the_backend_it_was_found_on(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    tmp_path: Path,
) -> None:
    """An orphan file discovered on a NON-default backend (no matching DB
    row anywhere) is adopted with `backend_id` pointing at the backend it
    was actually found on, plus a `file_locations` row for it -- not the
    default.
    """
    settings = get_settings()
    other_root = tmp_path / "backend-b-orphan"
    other_root.mkdir()
    other_backend = LocalStorageBackend(other_root)

    with base.sync_session() as session:
        storage_backends.create_backend_sync(session, settings, "A", LocalConfig(), is_default=True)
        other_row = storage_backends.create_backend_sync(
            session, settings, "B", LocalConfig(root=str(other_root))
        )
        other_id = other_row.id

    other_backend.write("dropped-folder/orphan.stl", [b"orphan-bytes-on-b"])

    scan_run = _run_scan_all_backends()

    assert scan_run.state == "done"
    assert scan_run.adopted == 1
    assert len(scan_run.report["adopted"]) == 1

    with base.sync_session() as session:
        adopted_file = session.execute(select(File)).scalars().one()
        assert adopted_file.backend_id == other_id
        location = session.get(FileLocation, (adopted_file.id, other_id))
        assert location is not None


async def test_multi_backend_scan_recognizes_replicas_and_does_not_adopt_them(
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    tmp_path: Path,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """A file replicated onto backend B (primary stays on A, plus a
    ``file_locations`` row for B) must be RECOGNIZED when B is walked -- its B
    location touched -- not mistaken for an orphan and adopted as a phantom
    duplicate model. Its primary ``backend_id`` must remain A.
    """
    settings = get_settings()
    other_root = tmp_path / "backend-b-replica"
    other_root.mkdir()
    other_backend = LocalStorageBackend(other_root)

    with base.sync_session() as session:
        default_row = storage_backends.create_backend_sync(
            session, settings, "A", LocalConfig(), is_default=True
        )
        other_row = storage_backends.create_backend_sync(
            session, settings, "B", LocalConfig(root=str(other_root))
        )
        default_id, other_id = default_row.id, other_row.id

    # Primary file on A (bytes via the shared `backend` fixture, rooted at A).
    model, rev = await _create_model_and_revision(db_session, "widget", "Widget")
    file = await seed_file(model, rev, "part.stl", b"replicated-bytes")
    await _sync_mtime(db_session, backend, file)
    file.backend_id = default_id
    # Replicate the SAME bytes onto B at the same storage_path, with an
    # un-verified `file_locations` row for B (proves the scan touches it).
    other_backend.write(file.storage_path, [b"replicated-bytes"])
    db_session.add(FileLocation(file_id=file.id, backend_id=other_id, verified_at=None))
    await db_session.commit()

    scan_run = _run_scan_all_backends()

    assert scan_run.state == "done"
    # The replica on B is recognized, not adopted as a phantom model.
    assert scan_run.adopted == 0
    assert scan_run.report["adopted"] == []
    with base.sync_session() as session:
        phantom = (
            session.execute(select(Model).where(Model.review_state == "adopted")).scalars().all()
        )
        assert phantom == []
        refreshed = session.get(File, file.id)
        assert refreshed.backend_id == default_id  # primary unchanged
        loc_b = session.get(FileLocation, (file.id, other_id))
        assert loc_b is not None
        assert loc_b.verified_at is not None  # the B replica was verified by the scan


# ---------------------------------------------------------------------------
# D2 (M6 Task 6): pass-2 N+1 batching + per-chunk checkpoint commits.
#
# `_FakeWalkBackend` isolates these from real disk I/O -- `walk` yields
# synthetic entries and `read` returns tiny deterministic bytes -- so the
# assertions below measure DB round trips and wall-clock, not I/O. Every
# synthetic file under one backend shares IDENTICAL content (one digest for
# the whole harness): that's what makes the harness deterministic and
# side-effect-free -- only the very first ever-seen file triggers the
# "brand new blob" branch (one best-effort pipeline dispatch total,
# regardless of file count N); every other file lands on the "known hash,
# no relink candidate" adopted-duplicate branch. A real scan with N
# distinct files would still batch the SAME round trips (the batching is
# per-chunk, not content-dependent) -- this just keeps the test's own
# incidental pipeline-dispatch cost from scaling with N.
# ---------------------------------------------------------------------------


class _FakeWalkBackend:
    """Synthetic in-memory backend for the pass-2 perf/query-count harness
    (Task 6 brief Step 5): ``walk`` yields ``n`` synthetic entries under
    ``prefix`` with zero real disk I/O, and ``read`` always returns the same
    small deterministic payload -- unique to THIS backend's ``prefix`` (not
    a shared class-level constant), so two harness runs against different
    prefixes never accidentally share a ``Blob`` row and skew each other's
    query count -- so every entry hashes to one shared digest (see module
    comment above).
    """

    def __init__(self, prefix: str, n: int) -> None:
        self._prefix = prefix
        self._n = n
        self.content = f"fake-pass2-bytes:{prefix}".encode()

    def walk(self, prefix: str = "") -> Iterator[EntryInfo]:
        mtime = datetime.now(UTC)
        for i in range(self._n):
            yield EntryInfo(key=f"{self._prefix}/{i:06d}.stl", size=len(self.content), mtime=mtime)

    def read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        yield self.content


def _count_statements(fn: Callable[[], None]) -> int:
    """Run ``fn``, counting every SQL statement executed against the worker
    sync engine (``app.tasks.base``) meanwhile."""
    engine = base.get_sync_engine()
    count = 0

    def _tick(*_args: object, **_kwargs: object) -> None:
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", _tick)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _tick)
    return count


async def _scan_and_count(db_session: AsyncSession, slug: str, n: int) -> int:
    await _create_model_and_revision(db_session, slug, slug)
    backend = _FakeWalkBackend(f"{slug}/rev-001_initial", n)
    scan_run_id = _create_scan_run()
    settings = get_settings()

    def _run() -> None:
        with base.sync_session() as session:
            scanner.run_scan(session, settings, backend, scan_run_id)

    return _count_statements(_run)


async def test_pass2_query_count_is_bounded_regardless_of_file_count(
    db_session: AsyncSession,
) -> None:
    """RED->GREEN pin for D2: pre-fix, `_reconcile_unknown` pays ~6 sync DB
    round trips per unknown file (`session.get(Blob)`, the adopt-target
    selects, the Blob/File insert-flushes), so the executed-statement count
    scales ~linearly with file count. Post-fix, pass 2 preloads adopt
    targets once and batches the blob-existence lookup + inserts per chunk,
    so the count should grow with chunk count, not file count -- both n=50
    and n=500 fit in one `_CHUNK` (500), so the two counts should be nearly
    identical (today they're roughly 10x apart).
    """
    count_50 = await _scan_and_count(db_session, "bulkq50", 50)
    count_500 = await _scan_and_count(db_session, "bulkq500", 500)
    print(f"\nD2 query-count harness: count_50={count_50} count_500={count_500}")
    assert count_500 < count_50 * 3


# ---------------------------------------------------------------------------
# Checkpointing: a mid-scan crash must keep earlier chunks' committed data.
# ---------------------------------------------------------------------------


async def test_checkpoint_commit_survives_mid_scan_crash(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2 checkpointing: pass 2 commits per chunk, so when a LATER chunk
    raises, EARLIER chunks' adopted rows must already be durably committed
    -- queryable from a totally fresh session/connection, not just visible
    within the now-crashed transaction. `scan_library` (the Celery task
    wrapping `run_scan`) still marks the run `failed` on the exception; this
    test pins that prior chunks' *data* survives regardless.
    """
    await _create_model_and_revision(db_session, "chkpt", "Checkpoint")
    monkeypatch.setattr(scanner, "_CHUNK", 2)

    backend = _FakeWalkBackend("chkpt/rev-001_initial", 4)  # -> two chunks of 2

    real_chunk = scanner._reconcile_unknown_chunk
    calls = 0

    def flaky_chunk(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("boom mid-scan")
        real_chunk(*args, **kwargs)

    monkeypatch.setattr(scanner, "_reconcile_unknown_chunk", flaky_chunk)

    scan_run_id = _create_scan_run()
    settings = get_settings()
    with pytest.raises(RuntimeError, match="boom mid-scan"), base.sync_session() as session:
        scanner.run_scan(session, settings, backend, scan_run_id)

    # A brand-new session/connection: proves the first chunk's two adopted
    # files were actually COMMITTED, not merely visible inside the crashed
    # transaction's own session.
    with base.sync_session() as fresh:
        files = fresh.execute(select(File)).scalars().all()
        assert len(files) == 2


# ---------------------------------------------------------------------------
# 50k-file perf harness (Task 6 brief Step 5): a regression tripwire, not a
# benchmark -- the query-count bound is the machine-independent signal, the
# wall-clock bound is a generous CI-safe backstop.
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_pass2_scales_to_50k_files_with_bounded_queries_and_time(
    migrated_db: str,
) -> None:
    settings = get_settings()
    with base.sync_session() as session:
        model = Model(slug="bulk50k", name="Bulk 50k")
        session.add(model)
        session.flush()
        revision = Revision(model_id=model.id, number=1, name="initial", dir_name="rev-001_initial")
        session.add(revision)
        session.flush()
        model.current_revision_id = revision.id
        session.commit()

    backend = _FakeWalkBackend("bulk50k/rev-001_initial", 50_000)
    scan_run_id = _create_scan_run()

    def _run() -> None:
        with base.sync_session() as session:
            scanner.run_scan(session, settings, backend, scan_run_id)

    start = time.monotonic()
    count = _count_statements(_run)
    elapsed = time.monotonic() - start
    print(f"\nD2 50k harness: statements={count} elapsed={elapsed:.2f}s")

    with base.sync_session() as session:
        scan_run = session.get(ScanRun, scan_run_id)
        assert scan_run.state == "done"
        assert scan_run.adopted == 50_000

    # ~100 chunks (50_000 / _CHUNK=500) worth of overhead, nowhere near one
    # round trip per file (which would be tens of thousands of statements).
    assert count < 1_000
    # Generous CI-safe tripwire -- see module comment.
    assert elapsed < 30
