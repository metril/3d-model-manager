"""Dashboard stats aggregation (R11-B item 13: ``GET /api/stats``).

Every number here is a cheap SQL aggregate (``count``/``sum``/``group by``)
-- one query per section, no new tables. Results are cached in-process for
``CACHE_TTL_S`` seconds (a plain module-level tuple, since there's only ever
one possible response) so a dashboard poll -- or several browser tabs
polling at once -- can't hammer the DB; ``get_stats`` is the only entry
point and callers never see the cache directly.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.collections import FollowedCollection
from app.models.enums import PrintSource
from app.models.inventory import Spool, SpoolUsage
from app.models.library import Blob, File, Material, Model, Print, Tag
from app.models.printing import PrintJob
from app.models.storage import StorageBackendRow
from app.models.system import Job
from app.schemas.library import ModelSummary
from app.schemas.prints import PrintOut
from app.schemas.stats import (
    FilamentPointOut,
    FilamentSeriesOut,
    FilamentStatsOut,
    FilesStats,
    JobsStats,
    MaterialUsageOut,
    ModelsStats,
    PrintsStats,
    RecentStats,
    StatsOut,
)
from app.services import layout
from app.services.app_config import get_app_config
from app.services.library import build_model_summaries

VALID_BUCKETS = ("day", "week", "month")

CACHE_TTL_S = 30.0

# `(stats, computed_at_monotonic)` -- module-level so it's shared across
# every request in this process; `None` until the first computation.
_cache: tuple[StatsOut, float] | None = None


def reset_stats_cache() -> None:
    """Clear the in-process cache. Production code never needs this (the
    TTL handles staleness); it exists so tests can force a recompute
    instead of silently depending on run order to see an empty cache."""
    global _cache
    _cache = None


async def _models_stats(db: AsyncSession) -> ModelsStats:
    total, favorites, archived, drafts = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Model.favorite.is_(True)),
                func.count().filter(Model.is_archived.is_(True)),
                # A "draft" is a model with no revision snapshot yet -- the
                # scanner/import/upload paths all set `current_revision_id`
                # as soon as one exists, so NULL here means "created but
                # nothing attached yet".
                func.count().filter(Model.current_revision_id.is_(None)),
            ).select_from(Model)
        )
    ).one()
    return ModelsStats(total=total, favorites=favorites, archived=archived, drafts=drafts)


async def _files_stats(db: AsyncSession) -> FilesStats:
    # Internal snapshot files (the cover-image snapshot -- R13a review fix)
    # are never user content; exclude them from every total below, same as
    # the model detail/files listing and zip export.
    not_snapshot = ~File.rel_path.startswith(layout.SNAPSHOT_PREFIX)

    total, bytes_total = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Blob.size), 0))
            .select_from(File)
            .join(Blob, File.blob_hash == Blob.hash)
            .where(not_snapshot)
        )
    ).one()

    by_backend_rows = (
        await db.execute(
            select(StorageBackendRow.name, func.coalesce(func.sum(Blob.size), 0))
            .select_from(File)
            .join(Blob, File.blob_hash == Blob.hash)
            .join(StorageBackendRow, File.backend_id == StorageBackendRow.id)
            .where(not_snapshot)
            .group_by(StorageBackendRow.name)
        )
    ).all()

    by_format_rows = (
        await db.execute(
            select(Blob.format, func.count())
            .select_from(File)
            .join(Blob, File.blob_hash == Blob.hash)
            .where(not_snapshot)
            .group_by(Blob.format)
        )
    ).all()

    return FilesStats(
        total=total,
        bytes_total=int(bytes_total),
        bytes_by_backend={name: int(size) for name, size in by_backend_rows},
        by_format={str(fmt): count for fmt, count in by_format_rows},
    )


async def _prints_stats(db: AsyncSession) -> PrintsStats:
    total, succeeded, failed, filament_g_total, duration_min_total, cost_total = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Print.result == "success"),
                func.count().filter(Print.result == "fail"),
                func.coalesce(func.sum(Print.filament_g), 0.0),
                func.coalesce(func.sum(Print.duration_min), 0),
                func.coalesce(func.sum(Print.total_cost), 0.0),
            ).select_from(Print)
        )
    ).one()
    return PrintsStats(
        total=total,
        succeeded=succeeded,
        failed=failed,
        filament_g_total=float(filament_g_total),
        duration_s_total=int(duration_min_total) * 60,
        cost_total=float(cost_total),
    )


async def _recent_stats(db: AsyncSession) -> RecentStats:
    cutoff = datetime.now(UTC) - timedelta(days=7)
    models_added_7d = (
        await db.execute(select(func.count()).select_from(Model).where(Model.created_at >= cutoff))
    ).scalar_one()
    prints_7d = (
        await db.execute(select(func.count()).select_from(Print).where(Print.printed_at >= cutoff))
    ).scalar_one()
    return RecentStats(models_added_7d=models_added_7d, prints_7d=prints_7d)


async def _jobs_stats(db: AsyncSession) -> JobsStats:
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    running, queued, failed_24h = (
        await db.execute(
            select(
                func.count().filter(Job.state == "running"),
                func.count().filter(Job.state == "queued"),
                func.count().filter(Job.state.in_(("failed", "dead")), Job.updated_at >= cutoff),
            ).select_from(Job)
        )
    ).one()
    return JobsStats(running=running, queued=queued, failed_24h=failed_24h)


async def _recent_models(db: AsyncSession, settings: Settings) -> list[ModelSummary]:
    """The 10 most recently created models (R13c dashboard), built via the
    SAME `build_model_summaries` aggregate-then-assemble path the gallery
    uses -- not a hand-rolled second summary shape."""
    stmt = (
        select(Model)
        .options(selectinload(Model.tags), selectinload(Model.category))
        .order_by(Model.created_at.desc(), Model.id.desc())
        .limit(10)
    )
    models = list((await db.execute(stmt)).scalars().unique().all())
    return await build_model_summaries(db, settings, models)


async def _recent_prints(db: AsyncSession) -> list[PrintOut]:
    """The 10 most recent print log entries (R13c dashboard), with the
    parent model's slug/name joined in (`PrintOut.model_slug`/`model_name`,
    populated only here -- the per-model listing already scopes to one
    model)."""
    stmt = (
        select(Print, Model.slug, Model.name)
        .outerjoin(Model, Model.id == Print.model_id)
        .options(selectinload(Print.material))
        .order_by(Print.printed_at.desc(), Print.id.desc())
        .limit(10)
    )
    rows = (await db.execute(stmt)).all()
    return [
        PrintOut.from_model(print_row, model_slug=slug, model_name=name)
        for print_row, slug, name in rows
    ]


async def _material_usage(db: AsyncSession) -> list[MaterialUsageOut]:
    """Total filament grams + print count per material (R13c dashboard,
    rewired in P5 Task 8): ``spool_usage`` (joined through ``Spool`` to a
    catalog ``Material`` when linked, else the spool's free-text
    ``material_type``) now covers every auto run exactly once -- the old
    proportional split of the slicer's ``est_filament`` estimate is gone,
    since each tray's actual charged grams already landed in
    ``spool_usage`` when the archive was recorded (``record_job_usage``).
    ``prints`` rows add only ``source=manual`` entries on top, so a manual
    log entry (never backed by ``spool_usage``) is still counted and an
    auto run is never double-counted. Same material name -> same row,
    whichever side contributed it."""
    usage_with_material = (
        await db.execute(
            select(
                Material.id,
                Material.name,
                func.coalesce(func.sum(SpoolUsage.grams), 0.0),
                func.count(SpoolUsage.id),
            )
            .select_from(SpoolUsage)
            .join(Spool, Spool.id == SpoolUsage.spool_id)
            .join(Material, Material.id == Spool.material_id)
            .group_by(Material.id, Material.name)
        )
    ).all()
    usage_without_material = (
        await db.execute(
            select(
                Spool.material_type,
                func.coalesce(func.sum(SpoolUsage.grams), 0.0),
                func.count(SpoolUsage.id),
            )
            .select_from(SpoolUsage)
            .join(Spool, Spool.id == SpoolUsage.spool_id)
            .where(Spool.material_id.is_(None))
            .group_by(Spool.material_type)
        )
    ).all()
    with_material = (
        await db.execute(
            select(
                Material.id,
                Material.name,
                func.coalesce(func.sum(Print.filament_g), 0.0),
                func.count(Print.id),
            )
            .join(Print, Print.material_id == Material.id)
            .where(Print.source == PrintSource.MANUAL)
            .group_by(Material.id, Material.name)
        )
    ).all()
    without_material = (
        await db.execute(
            select(
                Print.filament,
                func.coalesce(func.sum(Print.filament_g), 0.0),
                func.count(Print.id),
            )
            .where(
                Print.material_id.is_(None),
                Print.source == PrintSource.MANUAL,
                Print.filament.is_not(None),
                Print.filament != "",
            )
            .group_by(Print.filament)
        )
    ).all()

    by_material: dict[int, tuple[str, float, int]] = {}
    for material_id, name, grams, count in usage_with_material:
        by_material[material_id] = (name, float(grams), count)
    for material_id, name, grams, count in with_material:
        base_name, base_g, base_n = by_material.get(material_id, (name, 0.0, 0))
        by_material[material_id] = (base_name, base_g + float(grams), base_n + count)

    free: dict[str, tuple[str, float, int]] = {}
    for name, grams, count in usage_without_material:
        free[name.casefold()] = (name, float(grams), count)
    for name, grams, count in without_material:
        key = name.casefold()
        base_name, base_g, base_n = free.get(key, (name, 0.0, 0))
        free[key] = (base_name, base_g + float(grams), base_n + count)

    usage = [
        MaterialUsageOut(material_id=material_id, name=name, grams=grams, prints=count)
        for material_id, (name, grams, count) in by_material.items()
    ]
    usage += [
        MaterialUsageOut(material_id=None, name=name, grams=grams, prints=count)
        for name, grams, count in free.values()
    ]
    usage.sort(key=lambda row: row.grams, reverse=True)
    return usage


async def filament_stats(
    db: AsyncSession, settings: Settings, *, days: int, bucket: str, visible: set[int] | None
) -> FilamentStatsOut:
    """``GET /stats/filament`` (P5 Task 8): per-material and per-spool
    filament usage over time, bucketed by ``date_trunc(bucket,
    spool_usage.created_at)``. Not cached (unlike ``get_stats``) -- callers
    vary ``days``/``bucket`` per request, so a shared TTL cache would either
    thrash or serve the wrong window.

    ``visible`` (``visible_printer_ids(ctx)``, ``None`` = admin/all) scopes
    out usage tied to a print job on a printer the caller can't see, same
    as the archive routes; manual usage (no ``print_job_id``) is always
    visible, since it isn't tied to any printer.
    """
    if bucket not in VALID_BUCKETS:
        raise ValueError(f"invalid bucket: {bucket!r}")
    cfg = await get_app_config(db, settings)
    global_rate = cfg.filament_cost_per_kg
    cutoff = datetime.now(UTC) - timedelta(days=days)

    stmt = (
        select(
            SpoolUsage.spool_id,
            Spool.material_id,
            func.coalesce(Material.name, Spool.material_type).label("material_name"),
            Spool.color_hex,
            Spool.cost_per_kg,
            func.date_trunc(bucket, SpoolUsage.created_at).label("bucket"),
            func.sum(SpoolUsage.grams).label("grams"),
        )
        .select_from(SpoolUsage)
        .join(Spool, Spool.id == SpoolUsage.spool_id)
        .outerjoin(Material, Material.id == Spool.material_id)
        .outerjoin(PrintJob, PrintJob.id == SpoolUsage.print_job_id)
        .where(SpoolUsage.created_at >= cutoff)
        .group_by(
            SpoolUsage.spool_id,
            Spool.material_id,
            "material_name",
            Spool.color_hex,
            Spool.cost_per_kg,
            "bucket",
        )
        .order_by("bucket")
    )
    if visible is not None:
        stmt = stmt.where(or_(SpoolUsage.print_job_id.is_(None), PrintJob.printer_id.in_(visible)))
    rows = (await db.execute(stmt)).all()

    by_material: dict[str, dict[str, Any]] = {}
    by_spool: dict[str, dict[str, Any]] = {}
    for spool_id, material_id, material_name, color_hex, cost_per_kg, t, grams in rows:
        grams = float(grams)
        rate = cost_per_kg if cost_per_kg is not None else global_rate
        cost = grams / 1000 * rate
        point = FilamentPointOut(t=t, grams=grams, cost=cost)

        mkey = str(material_id) if material_id is not None else f"name:{material_name}"
        mseries = by_material.setdefault(
            mkey,
            {"key": mkey, "name": material_name, "color_hex": None, "points": []},
        )
        mseries["points"].append(point)

        skey = str(spool_id)
        sseries = by_spool.setdefault(
            skey,
            {"key": skey, "name": material_name, "color_hex": color_hex, "points": []},
        )
        sseries["points"].append(point)

    def _finish(series: dict[str, Any]) -> FilamentSeriesOut:
        points: list[FilamentPointOut] = series["points"]
        return FilamentSeriesOut(
            key=series["key"],
            name=series["name"],
            color_hex=series["color_hex"],
            total_g=sum(p.grams for p in points),
            total_cost=sum(p.cost for p in points),
            points=points,
        )

    return FilamentStatsOut(
        by_material=[_finish(s) for s in by_material.values()],
        by_spool=[_finish(s) for s in by_spool.values()],
    )


async def _compute_stats(db: AsyncSession, settings: Settings) -> StatsOut:
    models = await _models_stats(db)
    files = await _files_stats(db)
    tags = (await db.execute(select(func.count()).select_from(Tag))).scalar_one()
    collections = (
        await db.execute(select(func.count()).select_from(FollowedCollection))
    ).scalar_one()
    prints = await _prints_stats(db)
    recent = await _recent_stats(db)
    jobs = await _jobs_stats(db)
    recent_models = await _recent_models(db, settings)
    recent_prints = await _recent_prints(db)
    material_usage = await _material_usage(db)

    return StatsOut(
        models=models,
        files=files,
        tags=tags,
        collections=collections,
        prints=prints,
        recent=recent,
        jobs=jobs,
        recent_models=recent_models,
        recent_prints=recent_prints,
        material_usage=material_usage,
    )


async def get_stats(db: AsyncSession, settings: Settings) -> StatsOut:
    """Cached dashboard stats -- recomputed at most once every
    ``CACHE_TTL_S`` seconds process-wide."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[1] < CACHE_TTL_S:
        return _cache[0]
    stats = await _compute_stats(db, settings)
    _cache = (stats, now)
    return stats
