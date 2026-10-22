"""``GET /api/stats`` (R11-B item 13): dashboard aggregate counts.

Seeds a small dataset directly via the ORM (mirrors `seed_file`'s
direct-insert convention) and asserts each section of the response, plus
the 30s in-process cache TTL (`app.services.stats`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collections import FollowedCollection
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    BlobFormat,
    BlobKind,
    CollectionSyncMode,
    ImportSite,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
    PrintResult,
    PrintSource,
    SpoolUsageSource,
)
from app.models.inventory import Spool, SpoolUsage
from app.models.library import Blob, File, Material, Model, Print, Revision, Tag
from app.models.printing import PrintArchive, Printer, PrintJob
from app.models.storage import StorageBackendRow
from app.models.system import Job
from app.services import stats as stats_service

pytestmark = pytest.mark.usefixtures("library_root")


@pytest.fixture(autouse=True)
def _reset_stats_cache() -> None:
    """The stats cache is a module-level singleton shared across every test
    in the process; without a reset, `test_stats_sections` only passes when
    it happens to run before something else has cached a response."""
    stats_service.reset_stats_cache()


async def _make_model(db: AsyncSession, name: str, **kwargs: object) -> Model:
    model = Model(slug=name.lower().replace(" ", "-"), name=name, **kwargs)
    db.add(model)
    await db.flush()
    return model


async def _make_file(
    db: AsyncSession, model: Model, digest: str, size: int, fmt: BlobFormat
) -> File:
    blob = await db.get(Blob, digest)
    if blob is None:
        blob = Blob(hash=digest, size=size, kind=BlobKind.MESH, format=fmt)
        db.add(blob)
        await db.flush()
    revision = Revision(model_id=model.id, number=1, dir_name="r1")
    db.add(revision)
    await db.flush()
    model.current_revision_id = revision.id

    default_backend = (
        await db.execute(select(StorageBackendRow).where(StorageBackendRow.is_default.is_(True)))
    ).scalar_one()
    file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path="model.stl",
        storage_path=f"{model.slug}/r1/model.stl",
        backend_id=default_backend.id,
    )
    db.add(file)
    await db.flush()
    return file


async def test_stats_sections(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # A default backend row only gets lazily created on first real storage
    # access (`_ensure_default_backend_row`); seed one directly so the
    # by-backend breakdown below has something to join `files.backend_id`
    # against, same as a real install after its first write.
    db_session.add(
        StorageBackendRow(
            name="local", scheme="local", config={"backend": "local", "root": ""}, is_default=True
        )
    )
    await db_session.flush()

    # models: one draft (no revision), one with a file (favorite), one archived
    await _make_model(db_session, "Draft Model")
    with_file = await _make_model(db_session, "Filed Model", favorite=True)
    archived = await _make_model(db_session, "Archived Model", is_archived=True)
    await _make_file(db_session, with_file, "a" * 64, 1000, BlobFormat.STL)
    await _make_file(db_session, archived, "b" * 64, 2000, BlobFormat.THREEMF)

    db_session.add(Tag(name="one"))
    db_session.add(Tag(name="two"))

    db_session.add(
        FollowedCollection(
            site=ImportSite.THINGIVERSE,
            list_id="123",
            kind="collection",
            title="Some Collection",
            mode=CollectionSyncMode.AUTO,
        )
    )

    db_session.add(
        Print(
            model_id=with_file.id,
            result=PrintResult.SUCCESS,
            filament_g=50.5,
            duration_min=90,
        )
    )
    db_session.add(
        Print(
            model_id=with_file.id,
            result=PrintResult.FAIL,
            filament_g=10.0,
            duration_min=30,
        )
    )

    db_session.add(Job(id=uuid.uuid4(), type="scan_library", state="running"))
    db_session.add(Job(id=uuid.uuid4(), type="scan_library", state="queued"))
    db_session.add(
        Job(
            id=uuid.uuid4(),
            type="scan_library",
            state="failed",
            updated_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db_session.commit()

    response = await authenticated_client.get("/api/stats")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["models"] == {"total": 3, "favorites": 1, "archived": 1, "drafts": 1}

    assert body["files"]["total"] == 2
    assert body["files"]["bytes_total"] == 3000
    assert body["files"]["bytes_by_backend"] == {"local": 3000}
    assert body["files"]["by_format"] == {"stl": 1, "3mf": 1}

    assert body["tags"] == 2
    assert body["collections"] == 1

    assert body["prints"]["total"] == 2
    assert body["prints"]["succeeded"] == 1
    assert body["prints"]["failed"] == 1
    assert body["prints"]["filament_g_total"] == pytest.approx(60.5)
    assert body["prints"]["duration_s_total"] == 120 * 60

    assert body["recent"]["models_added_7d"] == 3
    assert body["recent"]["prints_7d"] == 2

    assert body["jobs"] == {"running": 1, "queued": 1, "failed_24h": 1}


async def test_stats_cache_ttl(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second request inside the 30s TTL reuses the cached result even
    though the underlying data changed; once the clock advances past the
    TTL, the next request recomputes."""
    fake_time = [1000.0]
    monkeypatch.setattr(stats_service.time, "monotonic", lambda: fake_time[0])

    first = await authenticated_client.get("/api/stats")
    assert first.status_code == 200
    assert first.json()["tags"] == 0

    db_session.add(Tag(name="fresh"))
    await db_session.commit()

    fake_time[0] += 5  # still inside the 30s TTL
    cached = await authenticated_client.get("/api/stats")
    assert cached.json()["tags"] == 0

    fake_time[0] += 30  # past the TTL
    refreshed = await authenticated_client.get("/api/stats")
    assert refreshed.json()["tags"] == 1


# ---------------------------------------------------------------------------
# R13c: recent_models, recent_prints, material_usage
# ---------------------------------------------------------------------------


async def test_stats_recent_models_and_prints(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    older = await _make_model(db_session, "Older Recent Model")
    newer = await _make_model(db_session, "Newer Recent Model")
    await db_session.flush()
    # Force a deterministic created_at ordering independent of insert speed.
    older.created_at = datetime.now(UTC) - timedelta(days=1)
    newer.created_at = datetime.now(UTC)
    db_session.add(
        Print(model_id=newer.id, result=PrintResult.SUCCESS, filament="Generic PLA", filament_g=5.0)
    )
    await db_session.commit()

    response = await authenticated_client.get("/api/stats")
    assert response.status_code == 200, response.text
    body = response.json()

    recent_model_ids = [m["id"] for m in body["recent_models"]]
    assert recent_model_ids.index(newer.id) < recent_model_ids.index(older.id)

    assert len(body["recent_prints"]) == 1
    recent_print = body["recent_prints"][0]
    assert recent_print["model_id"] == newer.id
    assert recent_print["model_slug"] == newer.slug
    assert recent_print["model_name"] == newer.name


async def test_stats_material_usage_grouped_by_material_and_filament_fallback(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _make_model(db_session, "Material Usage Model")
    material = Material(name="Usage PLA", kind="PLA")
    db_session.add(material)
    await db_session.flush()

    db_session.add(Print(model_id=model.id, material_id=material.id, filament_g=20.0))
    db_session.add(Print(model_id=model.id, material_id=material.id, filament_g=30.0))
    db_session.add(Print(model_id=model.id, filament="Generic ABS", filament_g=15.0))
    # No filament text and no material -- must be excluded from usage rows.
    db_session.add(Print(model_id=model.id, filament_g=99.0))
    await db_session.commit()

    response = await authenticated_client.get("/api/stats")
    assert response.status_code == 200, response.text
    usage = response.json()["material_usage"]

    by_material_row = next(row for row in usage if row["material_id"] == material.id)
    assert by_material_row["name"] == "Usage PLA"
    assert by_material_row["grams"] == pytest.approx(50.0)
    assert by_material_row["prints"] == 2

    fallback_row = next(row for row in usage if row["material_id"] is None)
    assert fallback_row["name"] == "Generic ABS"
    assert fallback_row["grams"] == pytest.approx(15.0)
    assert fallback_row["prints"] == 1

    # the material-less, filament-less print is excluded from usage rows
    assert sum(row["prints"] for row in usage) == 3


async def test_material_usage_from_spool_usage(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """P5 Task 8: an auto run is charged once via its `spool_usage` ledger
    row -- no more proportional split of the slicer's `est_filament`
    estimate. A manual print is still counted via the free-text
    `Print.filament` path. Same material name from either side merges into
    one row."""
    model = await _make_model(db_session, "Spool Usage Model")
    printer = Printer(
        name="p", kind=PrinterKind.BAMBU_LAN, host="10.0.0.9", serial="p", access_code_enc="x"
    )
    db_session.add(printer)
    await db_session.flush()
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="j",
        state=PrintJobState.FINISHED,
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add(
        PrintArchive(
            print_job_id=job.id,
            model_id=model.id,
            result=ArchiveResult.COMPLETED,
            fetch_state=ArchiveFetchState.DONE,
            est_filament=[{"tray": 0, "type": "PLA", "grams": 40.0}],
        )
    )
    spool = Spool(material_type="PLA")
    db_session.add(spool)
    await db_session.flush()
    db_session.add(
        SpoolUsage(
            spool_id=spool.id,
            print_job_id=job.id,
            grams=40.0,
            source=SpoolUsageSource.THREE_MF,
        )
    )
    db_session.add(
        Print(
            model_id=model.id,
            print_job_id=job.id,
            source=PrintSource.AUTO,
            filament_g=40.0,
            total_cost=2.5,
        )
    )
    # Manual print, free-text filament matching the spool's material name.
    db_session.add(Print(model_id=model.id, filament="PLA", filament_g=10.0))
    await db_session.commit()

    body = (await authenticated_client.get("/api/stats")).json()
    usage = {u["name"]: u for u in body["material_usage"]}
    assert usage["PLA"]["material_id"] is None
    assert usage["PLA"]["grams"] == pytest.approx(50.0)
    assert usage["PLA"]["prints"] == 2
    assert body["prints"]["cost_total"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# P5 Task 8: GET /stats/filament
# ---------------------------------------------------------------------------


async def test_filament_stats_per_material_and_spool(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    spool_a = Spool(material_type="PLA", cost_per_kg=40.0)
    spool_b = Spool(material_type="PETG")  # no price -- falls back to the global rate
    db_session.add_all([spool_a, spool_b])
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            SpoolUsage(
                spool_id=spool_a.id, grams=100.0, source=SpoolUsageSource.MANUAL, created_at=now
            ),
            SpoolUsage(
                spool_id=spool_a.id, grams=50.0, source=SpoolUsageSource.MANUAL, created_at=now
            ),
            SpoolUsage(
                spool_id=spool_b.id, grams=80.0, source=SpoolUsageSource.MANUAL, created_at=now
            ),
        ]
    )
    await db_session.commit()
    # Move the first row into last week's bucket so there are 2 buckets for spool_a.
    row = (
        await db_session.execute(
            select(SpoolUsage).where(SpoolUsage.spool_id == spool_a.id, SpoolUsage.grams == 100.0)
        )
    ).scalar_one()
    row.created_at = now - timedelta(days=10)
    await db_session.commit()

    response = await authenticated_client.get("/api/stats/filament?days=90&bucket=week")
    assert response.status_code == 200, response.text
    body = response.json()

    by_material = {s["name"]: s for s in body["by_material"]}
    assert by_material["PLA"]["total_g"] == pytest.approx(150.0)
    assert by_material["PLA"]["total_cost"] == pytest.approx(150.0 / 1000 * 40.0)
    assert by_material["PETG"]["total_g"] == pytest.approx(80.0)
    assert by_material["PETG"]["total_cost"] == pytest.approx(80.0 / 1000 * 20.0)  # global rate

    by_spool = {s["key"]: s for s in body["by_spool"]}
    spool_a_series = by_spool[str(spool_a.id)]
    assert spool_a_series["total_g"] == pytest.approx(150.0)
    assert len(spool_a_series["points"]) == 2
    spool_b_series = by_spool[str(spool_b.id)]
    assert spool_b_series["total_g"] == pytest.approx(80.0)
    assert spool_b_series["total_cost"] == pytest.approx(80.0 / 1000 * 20.0)


async def test_filament_stats_bucket_validation(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.get("/api/stats/filament?bucket=year")
    assert response.status_code == 422

    response = await authenticated_client.get("/api/stats/filament?days=0")
    assert response.status_code == 422

    response = await authenticated_client.get("/api/stats/filament?days=731")
    assert response.status_code == 422
