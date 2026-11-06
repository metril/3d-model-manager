"""PR-B regressions: commit-before-storage-delete, trash restore, queue
retry/enqueue integrity, batch claim, billing, file reaper, scanner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import blake3
import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.models.enums import BlobFormat, BlobKind, QueueState
from app.models.library import Blob, File, Model, PrintQueueEntry, Revision
from app.services import batches as batches_svc
from app.services import billing, library, queue, trash
from app.services import scanner as scanner_svc
from app.storage.local import LocalStorageBackend
from app.tasks import base

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _mk_model(client: httpx.AsyncClient, name: str, upload: bool = False) -> dict:
    m = (await client.post("/api/models", json={"name": name})).json()
    if upload:
        u = await client.put(
            "/api/uploads",
            params={
                "model_id": m["id"],
                "revision_id": m["current_revision"]["id"],
                "rel_path": "part.stl",
                "allow_duplicate": "true",
            },
            content=b"hello-world",
        )
        assert u.status_code == 201, u.text
    return m


# -- 2. commit DB delete before touching storage ------------------------------


async def test_hard_delete_survives_storage_failure(
    authenticated_client, db_session: AsyncSession, backend: LocalStorageBackend, monkeypatch
) -> None:
    m = await _mk_model(authenticated_client, "Boom Delete", upload=True)

    def _boom(self, key):
        raise OSError("disk gone")

    monkeypatch.setattr(LocalStorageBackend, "delete", _boom)
    model = await db_session.get(Model, m["id"])
    await library.hard_delete_model(db_session, backend, get_settings(), model)
    assert await db_session.scalar(select(Model.id).where(Model.id == m["id"])) is None


async def test_trash_purge_survives_storage_failure(
    authenticated_client, db_session: AsyncSession, monkeypatch
) -> None:
    m = await _mk_model(authenticated_client, "Boom Purge", upload=True)
    await authenticated_client.post("/api/models/bulk-delete", json={"ids": [m["id"]]})

    def _boom(self, key):
        raise OSError("disk gone")

    monkeypatch.setattr(LocalStorageBackend, "delete", _boom)
    with base.sync_session() as s:
        model = s.get(Model, m["id"])
        assert trash.purge_model_sync(s, get_settings(), model) is True
    assert await db_session.scalar(select(Model.id).where(Model.id == m["id"])) is None


# -- 3. trash restore resets queue bookkeeping --------------------------------


async def test_restore_resets_queue_entry(authenticated_client, db_session: AsyncSession) -> None:
    m = await _mk_model(authenticated_client, "Queued Trash")
    q = await authenticated_client.post("/api/queue", json={"model_id": m["id"]})
    assert q.status_code in (200, 201), q.text
    eid = q.json()["id"]
    await authenticated_client.post("/api/models/bulk-delete", json={"ids": [m["id"]]})
    e = await db_session.get(PrintQueueEntry, eid)
    await db_session.refresh(e)
    assert e.state == QueueState.HELD
    e.hold_reason = trash.MODEL_IN_TRASH
    e.attempts = 4
    await db_session.commit()
    r = await authenticated_client.post("/api/trash/restore", json={"ids": [m["id"]]})
    assert r.status_code == 200, r.text
    await db_session.refresh(e)
    assert e.hold_reason is None
    assert e.attempts == 0
    assert e.assigned_printer_id is None
    assert e.print_job_id is None
    assert e.state != QueueState.HELD


# -- 4. in-place retry clears scheduling leftovers ----------------------------


async def test_retry_in_place_clears_hold_stage_release(
    authenticated_client, db_session: AsyncSession, seed_file: Callable[..., Awaitable[File]]
) -> None:
    model = Model(slug="retry-clear", name="retry-clear")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(rev)
    await db_session.commit()
    f = await seed_file(
        model, rev, "job.gcode.3mf", b"gc",
        blob_format=BlobFormat.GCODE_3MF, blob_kind=BlobKind.SLICED,
    )  # fmt: skip
    eid = (await authenticated_client.post("/api/queue", json={"file_id": f.id})).json()["id"]
    e = await db_session.get(PrintQueueEntry, eid)
    e.state = QueueState.FAILED
    e.hold_reason = "previous_failed"
    e.stage = "prestart"
    e.released_at = datetime.now(UTC)
    await db_session.commit()
    r = await authenticated_client.post(f"/api/queue/{eid}/retry")
    assert r.status_code == 200, r.text
    await db_session.refresh(e)
    assert (e.hold_reason, e.stage, e.released_at) == (None, None, None)


# -- 5. concurrent bare enqueue returns the existing entry --------------------


async def test_concurrent_bare_enqueue_single_entry(authenticated_client) -> None:
    m = await _mk_model(authenticated_client, "Race Me")
    settings = get_settings()

    async def one():
        async with get_sessionmaker()() as s:
            return await queue.enqueue_model(s, settings, m["id"])

    results = await asyncio.gather(*[one() for _ in range(4)])
    assert len({out.id for out, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1


# -- 6. batch dispatch claims atomically -------------------------------------


async def test_batch_dispatch_loses_claim_race(
    authenticated_client, db_session: AsyncSession, seed_file, monkeypatch
) -> None:
    from fastapi import HTTPException

    from app.api.deps import AuthContext  # noqa: F401
    from app.models.library import PrintBatch

    model = Model(slug="batch-claim", name="batch-claim")
    db_session.add(model)
    await db_session.flush()
    rev = Revision(model_id=model.id, number=1, dir_name="rev-001_init")
    db_session.add(rev)
    await db_session.commit()
    f = await seed_file(
        model, rev, "job.gcode.3mf", b"gc",
        blob_format=BlobFormat.GCODE_3MF, blob_kind=BlobKind.SLICED,
    )  # fmt: skip
    r = await authenticated_client.post(
        "/api/batches", json={"name": "b", "file_id": f.id, "quantity": 1}
    )
    bid = r.json()["id"]
    stale = await db_session.get(PrintBatch, bid)
    await db_session.refresh(stale)
    await db_session.execute(
        text("update print_batches set status='dispatching' where id=:i"), {"i": bid}
    )
    await db_session.commit()

    async def _stale(db, batch_id):
        stale.status = "draft"  # caller read the batch before the other request flipped it
        return stale

    monkeypatch.setattr(batches_svc, "get_or_404", _stale)
    with pytest.raises(HTTPException) as ei:
        await batches_svc.dispatch_batch(db_session, get_settings(), bid, None)  # type: ignore[arg-type]
    assert ei.value.status_code == 409


# -- 7. dispatcher: failed primary is not "sent" ------------------------------


def test_failed_primary_send_not_counted_sent(redis_url: str, fake_adapter, monkeypatch) -> None:
    import redis as redis_lib

    from app.services import dispatcher
    from tests import test_dispatcher as td

    settings = get_settings()
    with base.sync_session() as s:
        model, _, file = td._seed_model_file(s, slug="not-sent")
        printer = td._seed_printer(s, name="NotSentP")
        admin_id = td._seed_admin(s)
        td._seed_entry(s, model=model, file=file, state=QueueState.READY, created_by=admin_id)
        pid = printer.id
    td._seed_state(redis_url, pid, td.IDLE_STATE)

    def _boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(dispatcher, "create_job_sync", _boom)
    client = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        counts = dispatcher.dispatch_once(s, client, settings)
    client.close()
    assert counts["sent"] == 0


# -- 8. billing ---------------------------------------------------------------


def test_charge_skipped_when_total_cost_unknown() -> None:
    item = PrintQueueEntry(id=1, cost_center_id=1)
    run = type("R", (), {"total_cost": None, "id": 1})()
    assert billing.charge(None, item, run) is None  # type: ignore[arg-type]


def test_month_start_uses_configured_timezone(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    tz = billing._local_tz()
    assert str(tz) == "Pacific/Auckland"
    monkeypatch.setenv("TZ", "Not/AZone")
    assert str(billing._local_tz()) == "UTC"
    assert Decimal("0") == 0


# -- 13. scanner: shared storage path + per-chunk re-check --------------------


async def test_scan_reconciles_every_row_sharing_a_path(
    db_session: AsyncSession, backend: LocalStorageBackend, seed_file
) -> None:
    from tests.test_scanner import _create_model_and_revision, _run_scan, _sync_mtime

    model, rev = await _create_model_and_revision(db_session, "shared-a", "Shared A")
    a = await seed_file(model, rev, "part.stl", b"same-bytes")
    await _sync_mtime(db_session, backend, a)
    b = File(
        revision_id=rev.id, blob_hash=a.blob_hash, rel_path="copy.stl",
        storage_path=a.storage_path, mtime=a.mtime,
    )  # fmt: skip
    db_session.add(b)
    await db_session.commit()
    a.verified_at = b.verified_at = None
    await db_session.commit()
    _run_scan(backend)
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.verified_at is not None and b.verified_at is not None


async def test_scan_pass2_skips_path_adopted_meanwhile(
    db_session: AsyncSession, backend: LocalStorageBackend, monkeypatch
) -> None:
    from tests.test_scanner import _create_model_and_revision, _run_scan

    model, rev = await _create_model_and_revision(db_session, "late-m", "Late M")
    key = f"{model.slug}/{rev.dir_name}/late.stl"
    backend.write(key, [b"late-bytes"])
    digest = blake3.blake3(b"late-bytes").hexdigest()
    # A concurrent ingest creates the row after the pass-1 snapshot: simulate by
    # inserting it when pass 2 begins.
    real = scanner_svc._preload_adopt_targets

    def _hook(session, deferred):
        session.add(Blob(hash=digest, size=10, kind=BlobKind.MESH, format=BlobFormat.STL))
        session.flush()
        session.add(
            File(revision_id=rev.id, blob_hash=digest, rel_path="late.stl", storage_path=key)
        )
        session.flush()
        return real(session, deferred)

    monkeypatch.setattr(scanner_svc, "_preload_adopt_targets", _hook)
    _run_scan(backend)
    n = await db_session.scalar(
        text("select count(*) from files where storage_path = :p"), {"p": key}
    )
    assert n == 1
