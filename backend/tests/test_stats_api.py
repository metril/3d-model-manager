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


async def test_filament_stats_merges_same_material_points_by_bucket(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Item 4: two spools of the SAME material in the SAME bucket must
    merge into one `by_material` point, not two duplicate-`t` points."""
    spool_a = Spool(material_type="PLA")
    spool_b = Spool(material_type="PLA")
    db_session.add_all([spool_a, spool_b])
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            SpoolUsage(
                spool_id=spool_a.id, grams=100.0, source=SpoolUsageSource.MANUAL, created_at=now
            ),
            SpoolUsage(
                spool_id=spool_b.id, grams=50.0, source=SpoolUsageSource.MANUAL, created_at=now
            ),
        ]
    )
    await db_session.commit()

    response = await authenticated_client.get("/api/stats/filament?days=90&bucket=week")
    assert response.status_code == 200, response.text
    body = response.json()

    by_material = {s["name"]: s for s in body["by_material"]}
    pla = by_material["PLA"]
    assert len(pla["points"]) == 1
    assert pla["points"][0]["grams"] == pytest.approx(150.0)
    assert pla["total_g"] == pytest.approx(150.0)


async def test_filament_stats_bucket_validation(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.get("/api/stats/filament?bucket=year")
    assert response.status_code == 422

    response = await authenticated_client.get("/api/stats/filament?days=0")
    assert response.status_code == 422

    response = await authenticated_client.get("/api/stats/filament?days=731")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Q8b widgets
# ---------------------------------------------------------------------------


async def _printer(db: AsyncSession, serial: str) -> Printer:
    p = Printer(
        name=f"name-{serial}",
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.9",
        serial=serial,
        access_code_enc="x",
    )
    db.add(p)
    await db.flush()
    return p


async def _run(
    db: AsyncSession,
    printer: Printer,
    *,
    result: ArchiveResult = ArchiveResult.COMPLETED,
    start: datetime,
    duration_s: int = 3600,
    est_s: int | None = None,
    error: str | None = None,
    hms: list | None = None,
    grams: float | None = None,
    filament: str | None = None,
    name: str = "job",
) -> PrintArchive:
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name=name,
        state=PrintJobState.FINISHED,
        started_at=start,
        finished_at=start + timedelta(seconds=duration_s),
        printer_error=error,
        raw_status={"print": {"hms": hms}} if hms else None,
    )
    db.add(job)
    await db.flush()
    arc = PrintArchive(
        print_job_id=job.id,
        result=result,
        fetch_state=ArchiveFetchState.DONE,
        duration_s=duration_s,
        slicer_meta={"print_time_s": est_s} if est_s else None,
    )
    db.add(arc)
    await db.flush()
    db.add(
        Print(
            print_job_id=job.id,
            archive_id=arc.id,
            source=PrintSource.AUTO,
            result={
                ArchiveResult.COMPLETED: PrintResult.SUCCESS,
                ArchiveResult.FAILED: PrintResult.FAIL,
            }.get(result, PrintResult.PARTIAL),
            filament_g=grams,
            filament=filament,
            duration_min=duration_s // 60,
        )
    )
    await db.flush()
    return arc


async def test_stats_dashboard_widgets(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    a = await _printer(db_session, "A")
    b = await _printer(db_session, "B")
    # 2026-10-19 is a Monday; anchor inside the 365-day window relative to now.
    mon = (datetime.now(UTC) - timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
    mon -= timedelta(days=mon.weekday())
    done1 = await _run(
        db_session,
        a,
        start=mon,
        duration_s=3600,
        est_s=3000,
        grams=40.0,
        filament="PLA",
        name="big",
    )
    await _run(db_session, a, start=mon + timedelta(hours=2), duration_s=1800, est_s=1800)
    await _run(
        db_session,
        a,
        result=ArchiveResult.FAILED,
        start=mon + timedelta(hours=5),
        error="50348044",
        hms=[{"attr": 196866, "code": 65537}],
        grams=5.0,
        filament="PETG, PLA",
    )
    await _run(db_session, b, result=ArchiveResult.CANCELED, start=mon + timedelta(days=1))
    spool = Spool(material_type="PLA")
    db_session.add(spool)
    await db_session.flush()
    db_session.add(SpoolUsage(spool_id=spool.id, grams=12.0, source=SpoolUsageSource.MANUAL))
    await db_session.commit()

    r = await authenticated_client.get("/api/stats/dashboard?days=30")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["success_rate"] == {
        "total": 4,
        "completed": 2,
        "failed": 1,
        "canceled": 1,
        "stopped": 0,
        "success_rate": 0.5,
    }
    ta = d["time_accuracy"]
    assert ta["count"] == 2
    assert ta["mean_delta_pct"] == pytest.approx(10.0)
    assert sum(x["count"] for x in ta["distribution"]) == 2
    assert {p["archive_id"] for p in ta["points"]} >= {done1.id}
    f = d["failures"]
    assert f["total_failed"] == 1
    assert f["by_error_code"] == [{"key": "50348044", "count": 1}]
    assert f["by_hms"] == [{"key": "00030102_00010001", "count": 1}]
    assert f["by_printer"][0]["failed"] == 1 and f["by_printer"][0]["total"] == 3
    assert {m["key"] for m in f["by_material"]} == {"PETG", "PLA"}
    hm = d["heatmap"]
    assert hm["counts"][0][9] == 1 and hm["max"] >= 1
    assert sum(map(sum, hm["counts"])) == 4
    rec = d["records"]
    assert rec["longest_print"]["archive_id"] == done1.id and rec["longest_print"]["value"] == 3600
    assert rec["most_filament"]["value"] == 40.0 and rec["most_filament"]["name"] == "big"
    assert rec["busiest_day"]["prints"] == 3
    pa = next(p for p in d["printers"] if p["printer_id"] == a.id)
    assert pa["jobs"] == 3 and pa["completed"] == 2 and pa["hours"] == 2.5
    assert pa["filament_g"] == 45.0
    assert d["filament_trends"] == [
        {"week": d["filament_trends"][0]["week"], "material": "PLA", "grams": 12.0}
    ]

    # individual endpoints agree with the dashboard payload
    for path, key in [
        ("success-rate", "success_rate"),
        ("time-accuracy", "time_accuracy"),
        ("failures", "failures"),
        ("heatmap", "heatmap"),
        ("records", "records"),
        ("printers", "printers"),
        ("filament-trends", "filament_trends"),
    ]:
        resp = await authenticated_client.get(f"/api/stats/{path}?days=30")
        assert resp.status_code == 200, (path, resp.text)
        assert resp.json() == d[key], path


async def test_stats_dashboard_empty_and_window(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    a = await _printer(db_session, "A")
    await _run(db_session, a, start=datetime.now(UTC) - timedelta(days=60))
    await db_session.commit()
    d = (await authenticated_client.get("/api/stats/dashboard?days=30")).json()
    assert d["success_rate"]["total"] == 0 and d["success_rate"]["success_rate"] is None
    assert d["records"] == {"longest_print": None, "most_filament": None, "busiest_day": None}
    assert d["printers"] == [] and d["heatmap"]["max"] == 0
    d = (await authenticated_client.get("/api/stats/dashboard?days=90")).json()
    assert d["success_rate"]["total"] == 1
    assert (await authenticated_client.get("/api/stats/dashboard?days=0")).status_code == 422


async def test_stats_dashboard_visibility_filtered(
    make_user, make_group, login_as, db_session: AsyncSession
) -> None:
    a = await _printer(db_session, "A")
    b = await _printer(db_session, "B")
    now = datetime.now(UTC) - timedelta(hours=5)
    await _run(db_session, a, start=now)
    await _run(db_session, b, start=now, result=ArchiveResult.FAILED)
    await db_session.commit()
    viewer = await make_user("viewer")
    await make_group("g", members=[viewer], printer_access={a.id: (True, False, False)})
    client = await login_as(viewer)
    d = (await client.get("/api/stats/dashboard")).json()
    assert d["success_rate"]["total"] == 1 and d["success_rate"]["failed"] == 0
    assert [p["printer_id"] for p in d["printers"]] == [a.id]


async def test_filament_stats_buckets_day_in_tz(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from zoneinfo import ZoneInfo

    spool = Spool(material_type="PLA")
    db_session.add(spool)
    await db_session.flush()
    # 02:00 UTC is 18:00/19:00 the previous evening in Los Angeles
    at = datetime.now(UTC).replace(hour=2, minute=0, second=0, microsecond=0) - timedelta(days=2)
    db_session.add(
        SpoolUsage(spool_id=spool.id, grams=10.0, source=SpoolUsageSource.MANUAL, created_at=at)
    )
    await db_session.commit()
    base = "/api/stats/filament?days=30&bucket=day"
    utc = (await authenticated_client.get(base)).json()["by_spool"][0]["points"][0]["t"]
    la = (await authenticated_client.get(base + "&tz=America/Los_Angeles")).json()
    la_t = la["by_spool"][0]["points"][0]["t"]
    assert datetime.fromisoformat(utc).astimezone(UTC).date() == at.date()
    la_day = datetime.fromisoformat(la_t).astimezone(ZoneInfo("America/Los_Angeles")).date()
    assert la_day == at.date() - timedelta(days=1)
    assert (await authenticated_client.get(base + "&tz=Nope/Zone")).status_code == 422
