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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.collections import FollowedCollection
from app.models.enums import PrintSource
from app.models.library import Blob, File, Material, Model, Print, Tag
from app.models.printing import PrintArchive
from app.models.storage import StorageBackendRow
from app.models.system import Job
from app.schemas.library import ModelSummary
from app.schemas.prints import PrintOut
from app.schemas.stats import (
    FilesStats,
    JobsStats,
    MaterialUsageOut,
    ModelsStats,
    PrintsStats,
    RecentStats,
    StatsOut,
)
from app.services import layout
from app.services.library import build_model_summaries

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
    """Total filament grams + print count per material (R13c dashboard),
    grouped by ``Material`` where a print resolved to one, falling back to
    the free-text ``Print.filament`` snapshot for prints that didn't
    (``material_id: null`` there) -- two group-bys, not a per-print loop."""
    with_material = (
        await db.execute(
            select(
                Material.id,
                Material.name,
                func.coalesce(func.sum(Print.filament_g), 0.0),
                func.count(Print.id),
            )
            .join(Print, Print.material_id == Material.id)
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
                Print.source != PrintSource.AUTO,
                Print.filament.is_not(None),
                Print.filament != "",
            )
            .group_by(Print.filament)
        )
    ).all()
    # Auto runs: split the measured grams across the archive's filament types
    # in proportion to the slicer estimate.
    auto_rows = (
        await db.execute(
            select(Print.filament_g, PrintArchive.est_filament)
            .join(PrintArchive, PrintArchive.print_job_id == Print.print_job_id)
            .where(Print.source == PrintSource.AUTO, Print.filament_g.is_not(None))
        )
    ).all()
    auto: dict[str, tuple[str, float, int]] = {}
    for run_g, est in auto_rows:
        per_type: dict[str, float] = {}
        for entry in est or []:
            name = str(entry.get("type") or "").strip()
            if name:
                per_type[name] = per_type.get(name, 0.0) + float(entry.get("grams") or 0.0)
        total_est = sum(per_type.values())
        if total_est <= 0:
            continue
        for name, g in per_type.items():
            key = name.casefold()
            _, grams, count = auto.get(key, (name, 0.0, 0))
            auto[key] = (name, grams + g * run_g / total_est, count + 1)

    usage = [
        MaterialUsageOut(material_id=material_id, name=name, grams=float(grams), prints=count)
        for material_id, name, grams, count in with_material
    ]
    free: dict[str, tuple[str, float, int]] = {
        filament.casefold(): (filament, float(grams), count)
        for filament, grams, count in without_material
    }
    for key, (name, grams, count) in auto.items():
        base_name, base_g, base_n = free.get(key, (name, 0.0, 0))
        free[key] = (base_name, base_g + grams, base_n + count)
    usage += [
        MaterialUsageOut(material_id=None, name=name, grams=grams, prints=count)
        for name, grams, count in free.values()
    ]
    usage.sort(key=lambda row: row.grams, reverse=True)
    return usage


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
