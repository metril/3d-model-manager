"""Q8b stats widgets: success rate, time accuracy, failure analysis, heatmap,
records, per-printer stats, filament trends. ``load_rows`` is ONE joined
query over the window; every widget is a pure fold over those rows, so the
dashboard payload costs two queries total (rows + filament trends)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ArchiveResult
from app.models.inventory import Spool, SpoolUsage
from app.models.library import Material, Print
from app.models.printing import PrintArchive, Printer, PrintJob
from app.schemas.stats import (
    BusiestDayOut,
    FailureAnalysisOut,
    FailureCountOut,
    FailureGroupOut,
    FilamentTrendPointOut,
    HeatmapOut,
    PrinterStatsOut,
    RecordEntryOut,
    RecordsOut,
    StatsDashboardOut,
    SuccessRateOut,
    TimeAccuracyBucketOut,
    TimeAccuracyOut,
    TimeAccuracyPointOut,
)

_ACCURACY_EDGES = [
    (-20, "<-20%"),
    (-10, "-20..-10%"),
    (-5, "-10..-5%"),
    (5, "-5..5%"),
    (10, "5..10%"),
    (20, "10..20%"),
]
_ACCURACY_LAST = ">20%"
_POINT_CAP = 200


@dataclass(slots=True)
class Row:
    archive_id: int
    printer_id: int
    printer_name: str
    when: datetime
    started_at: datetime | None
    result: str
    duration_s: int | None
    est_s: int | None
    name: str | None
    error: str | None
    hms: object
    filament_g: float | None
    materials: list[str]


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _int(v: object) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def load_rows(db: AsyncSession, *, days: int, visible: set[int] | None) -> list[Row]:
    when = func.coalesce(PrintJob.finished_at, PrintArchive.created_at)
    stmt = (
        select(
            PrintArchive.id,
            PrintJob.printer_id,
            Printer.name,
            when.label("when"),
            PrintJob.started_at,
            PrintArchive.result,
            PrintArchive.duration_s,
            PrintArchive.slicer_meta["print_time_s"].astext,
            PrintJob.subtask_name,
            PrintJob.printer_error,
            func.coalesce(PrintJob.raw_status["print"]["hms"], PrintJob.raw_status["hms"]),
            Print.filament_g,
            Print.filament,
        )
        .select_from(PrintArchive)
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .join(Printer, Printer.id == PrintJob.printer_id)
        .outerjoin(Print, Print.print_job_id == PrintJob.id)
        .where(when >= _since(days))
        .order_by(when)
    )
    if visible is not None:
        stmt = stmt.where(PrintJob.printer_id.in_(visible))
    out: list[Row] = []
    for r in (await db.execute(stmt)).all():
        out.append(
            Row(
                archive_id=r[0],
                printer_id=r[1],
                printer_name=r[2],
                when=r[3],
                started_at=r[4],
                result=getattr(r[5], "value", r[5]),
                duration_s=r[6],
                est_s=_int(r[7]),
                name=r[8],
                error=r[9],
                hms=r[10],
                filament_g=r[11],
                materials=[m.strip() for m in (r[12] or "").split(",") if m.strip()],
            )
        )
    return out


_DONE = ArchiveResult.COMPLETED.value
_FAIL = ArchiveResult.FAILED.value


def success_rate(rows: list[Row]) -> SuccessRateOut:
    c = Counter(r.result for r in rows)
    total = len(rows)
    return SuccessRateOut(
        total=total,
        completed=c[_DONE],
        failed=c[_FAIL],
        canceled=c[ArchiveResult.CANCELED.value],
        stopped=c[ArchiveResult.STOPPED.value],
        success_rate=round(c[_DONE] / total, 4) if total else None,
    )


def time_accuracy(rows: list[Row]) -> TimeAccuracyOut:
    pts = [
        TimeAccuracyPointOut(
            archive_id=r.archive_id,
            printer_id=r.printer_id,
            estimated_s=r.est_s,
            actual_s=r.duration_s,
            delta_pct=round((r.duration_s - r.est_s) / r.est_s * 100, 2),
        )
        for r in rows
        if r.result == _DONE and r.est_s and r.est_s > 0 and r.duration_s is not None
    ]
    counts = Counter()
    for p in pts:
        label = next((lb for edge, lb in _ACCURACY_EDGES if p.delta_pct < edge), _ACCURACY_LAST)
        counts[label] += 1
    labels = [lb for _, lb in _ACCURACY_EDGES] + [_ACCURACY_LAST]
    n = len(pts)
    return TimeAccuracyOut(
        count=n,
        mean_delta_pct=round(sum(p.delta_pct for p in pts) / n, 2) if n else None,
        mean_abs_delta_pct=round(sum(abs(p.delta_pct) for p in pts) / n, 2) if n else None,
        distribution=[TimeAccuracyBucketOut(label=lb, count=counts[lb]) for lb in labels],
        points=pts[-_POINT_CAP:],
    )


def _hms_keys(hms: object) -> list[str]:
    keys: list[str] = []
    for h in hms if isinstance(hms, list) else []:
        if not isinstance(h, dict):
            continue
        attr, code = _int(h.get("attr")), _int(h.get("code"))
        if attr is not None and code is not None:
            keys.append(f"{attr:08X}_{code:08X}")
    return keys


def _groups(items: dict[str, tuple[int, int, str | None]]) -> list[FailureGroupOut]:
    out = [
        FailureGroupOut(
            key=k, name=name, failed=f, total=t, failure_rate=round(f / t, 4) if t else 0.0
        )
        for k, (f, t, name) in items.items()
        if f
    ]
    return sorted(out, key=lambda g: (-g.failed, g.key))


def failure_analysis(rows: list[Row]) -> FailureAnalysisOut:
    codes: Counter[str] = Counter()
    hms: Counter[str] = Counter()
    printers: dict[str, list] = defaultdict(lambda: [0, 0, None])
    mats: dict[str, list] = defaultdict(lambda: [0, 0, None])
    failed_total = 0
    for r in rows:
        bad = r.result == _FAIL
        p = printers[str(r.printer_id)]
        p[1] += 1
        p[2] = r.printer_name
        for m in r.materials:
            mats[m][1] += 1
        if not bad:
            continue
        failed_total += 1
        p[0] += 1
        for m in r.materials:
            mats[m][0] += 1
        if r.error:
            codes[r.error] += 1
        for k in _hms_keys(r.hms):
            hms[k] += 1

    def counts(c: Counter[str]) -> list[FailureCountOut]:
        return [
            FailureCountOut(key=k, count=n)
            for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    return FailureAnalysisOut(
        total_failed=failed_total,
        by_error_code=counts(codes),
        by_hms=counts(hms),
        by_printer=_groups({k: tuple(v) for k, v in printers.items()}),
        by_material=_groups({k: tuple(v) for k, v in mats.items()}),
    )


def heatmap(rows: list[Row]) -> HeatmapOut:
    grid = [[0] * 24 for _ in range(7)]
    for r in rows:
        t = (r.started_at or r.when).astimezone(UTC)
        grid[t.weekday()][t.hour] += 1
    return HeatmapOut(counts=grid, max=max(max(line) for line in grid))


def records(rows: list[Row]) -> RecordsOut:
    def entry(best: Row | None, value: float | None) -> RecordEntryOut | None:
        if best is None or value is None:
            return None
        return RecordEntryOut(
            archive_id=best.archive_id,
            name=best.name,
            value=float(value),
            printer_id=best.printer_id,
        )

    timed = [r for r in rows if r.duration_s is not None]
    longest = max(timed, key=lambda r: r.duration_s, default=None)
    fil = [r for r in rows if r.filament_g]
    heavy = max(fil, key=lambda r: r.filament_g, default=None)
    per_day = Counter(r.when.astimezone(UTC).date().isoformat() for r in rows)
    busiest = max(per_day.items(), key=lambda kv: (kv[1], kv[0]), default=None)
    return RecordsOut(
        longest_print=entry(longest, longest.duration_s if longest else None),
        most_filament=entry(heavy, heavy.filament_g if heavy else None),
        busiest_day=BusiestDayOut(date=busiest[0], prints=busiest[1]) if busiest else None,
    )


def printer_stats(rows: list[Row]) -> list[PrinterStatsOut]:
    acc: dict[int, dict] = {}
    for r in rows:
        a = acc.setdefault(
            r.printer_id, {"name": r.printer_name, "s": 0, "jobs": 0, "done": 0, "g": 0.0}
        )
        a["jobs"] += 1
        a["s"] += r.duration_s or 0
        a["done"] += r.result == _DONE
        a["g"] += r.filament_g or 0.0
    return [
        PrinterStatsOut(
            printer_id=pid,
            name=a["name"],
            hours=round(a["s"] / 3600, 2),
            jobs=a["jobs"],
            completed=a["done"],
            success_rate=round(a["done"] / a["jobs"], 4),
            filament_g=round(a["g"], 1),
        )
        for pid, a in sorted(acc.items(), key=lambda kv: (-kv[1]["jobs"], kv[0]))
    ]


async def filament_trends(
    db: AsyncSession, *, days: int, visible: set[int] | None
) -> list[FilamentTrendPointOut]:
    week = func.date_trunc("week", SpoolUsage.created_at).label("week")
    mat = func.coalesce(Material.name, Spool.material_type).label("material")
    stmt = (
        select(week, mat, func.sum(SpoolUsage.grams))
        .select_from(SpoolUsage)
        .join(Spool, Spool.id == SpoolUsage.spool_id)
        .outerjoin(Material, Material.id == Spool.material_id)
        .outerjoin(PrintJob, PrintJob.id == SpoolUsage.print_job_id)
        .where(SpoolUsage.created_at >= _since(days))
        .group_by(week, mat)
        .order_by(week, mat)
    )
    if visible is not None:
        stmt = stmt.where((SpoolUsage.print_job_id.is_(None)) | (PrintJob.printer_id.in_(visible)))
    return [
        FilamentTrendPointOut(week=w, material=m or "Unknown", grams=round(float(g), 1))
        for w, m, g in (await db.execute(stmt)).all()
    ]


async def dashboard(db: AsyncSession, *, days: int, visible: set[int] | None) -> StatsDashboardOut:
    rows = await load_rows(db, days=days, visible=visible)
    return StatsDashboardOut(
        days=days,
        success_rate=success_rate(rows),
        time_accuracy=time_accuracy(rows),
        failures=failure_analysis(rows),
        heatmap=heatmap(rows),
        records=records(rows),
        printers=printer_stats(rows),
        filament_trends=await filament_trends(db, days=days, visible=visible),
    )
