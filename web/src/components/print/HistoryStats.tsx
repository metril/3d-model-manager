/**
 * The history page's stats tab (P3 Task 9): the library-wide overview that
 * used to be `DashboardPage`/`DashboardContent`, rebuilt on the shared
 * `StatTile` primitive (P3 Task 4) and extended with a "Runs (14d)"
 * sparkline fed by `useArchives()` + `dailyRunCounts` -- the stats tab's
 * only printer-specific metric, so it keeps working even with the runs
 * tab hidden behind `printer_enabled=false`.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { useArchives } from "@/api/archives";
import { ApiError } from "@/api/client";
import { useFilamentStats, useStats } from "@/api/stats";
import type { FilamentSeriesOut, PrintResult } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/ui/sparkline";
import { StatTile } from "@/components/ui/stat-tile";
import { SegmentedControl } from "@/components/viewer/SegmentedControl";
import { formatCost, formatDate, humanizeBytes } from "@/lib/format";
import { formatIcon } from "@/lib/formatMeta";
import { dailyRunCounts } from "@/lib/runSeries";
import { useTranslation } from "react-i18next";

// Duplicated from `PrintsTab.tsx` (not exported there) -- keep in sync if the
// result vocabulary changes.
const RESULT_LABELS: Record<PrintResult, string> = {
  success: "histStats.result.success",
  fail: "histStats.result.fail",
  partial: "histStats.result.partial",
};
const RESULT_BADGE: Record<PrintResult, { variant: "destructive" | "outline"; className?: string }> = {
  success: { variant: "outline", className: "bg-success/15 text-success" },
  fail: { variant: "destructive" },
  partial: { variant: "outline", className: "bg-warning/15 text-warning" },
};

/** A labeled horizontal bar, its width relative to `max` -- the CSS-only
 * "chart" the Global Constraints bundle rule calls for. */
function Bar({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-foreground">{label}</span>
        <span className="text-muted-foreground">{value}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

const FILAMENT_RANGES = [30, 90, 365] as const;
type FilamentRange = `${(typeof FILAMENT_RANGES)[number]}`;
type FilamentView = "material" | "spool";

/** One series row (P5 Task 12): swatch + name, total grams/cost, and a
 * grams-per-bucket sparkline. A spool row links to its inventory page
 * (plain `<a>`, not the router's `Link` -- `/inventory/spools` doesn't
 * declare a `spool` search param, so this is a simple deep link rather
 * than a typed in-app navigation). */
function FilamentSeriesRow({ series, kind }: { series: FilamentSeriesOut; kind: FilamentView }) {
  const { t } = useTranslation();
  const row = (
    <div className="flex items-center gap-3 py-2">
      <FilamentChip color={series.color_hex ?? undefined} label={series.name} />
      <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">
        {Math.round(series.total_g)} g
      </span>
      <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">
        {formatCost(series.total_cost)}
      </span>
      <Sparkline
        values={series.points.map((p) => p.grams)}
        aria-label={t("histStats.gramsPerBucket", { name: series.name })}
        className="ml-auto"
      />
    </div>
  );
  if (kind === "spool") {
    return (
      <a
        href={`/inventory/spools?spool=${series.key}`}
        className="block rounded-[6px] hover:bg-accent/40"
      >
        {row}
      </a>
    );
  }
  return row;
}

/** Filament usage section (P5 Task 12): per-material/per-spool grams+cost
 * over a 30/90/365-day range, fed by `GET /stats/filament`. */
function FilamentUsageSection() {
  const { t } = useTranslation();
  const [view, setView] = useState<FilamentView>("material");
  const [range, setRange] = useState<FilamentRange>("90");
  const filamentStats = useFilamentStats({ days: Number(range), bucket: "week" });
  const series = (view === "material" ? filamentStats.data?.by_material : filamentStats.data?.by_spool) ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>{t("histStats.filamentUsage")}</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl<FilamentView>
            label={t("histStats.viewBy")}
            options={["material", "spool"]}
            labels={{ material: t("histStats.byMaterial"), spool: t("histStats.bySpool") }}
            value={view}
            onChange={setView}
          />
          <SegmentedControl<FilamentRange>
            label={t("histStats.range")}
            options={["30", "90", "365"]}
            labels={{ "30": "30d", "90": "90d", "365": "365d" }}
            value={range}
            onChange={setRange}
          />
        </div>
      </CardHeader>
      <CardContent>
        {filamentStats.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : series.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("histStats.noFilament")}</p>
        ) : (
          <div className="divide-y divide-border">
            {series.map((s) => (
              <FilamentSeriesRow key={s.key} series={s} kind={view} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function HistoryStats() {
  const { t } = useTranslation();
  const statsQuery = useStats();
  const archivesQuery = useArchives();

  if (statsQuery.isLoading) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (statsQuery.isError || !statsQuery.data) {
    return (
      <Card className="mx-auto mt-12 max-w-md">
        <CardHeader className="items-center text-center">
          <CardTitle>{t("histStats.loadError")}</CardTitle>
          <CardDescription>
            {statsQuery.error instanceof ApiError ? statsQuery.error.detail : t("histStats.wentWrong")}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const stats = statsQuery.data;
  const runCounts = dailyRunCounts(archivesQuery.data ?? []);
  const formatEntries = Object.entries(stats.files.by_format).sort(([, a], [, b]) => b - a);
  const maxFormatCount = Math.max(1, ...formatEntries.map(([, count]) => count));
  const backendEntries = Object.entries(stats.files.bytes_by_backend).sort(([, a], [, b]) => b - a);
  const maxBackendBytes = Math.max(1, ...backendEntries.map(([, bytes]) => bytes));
  const maxMaterialGrams = Math.max(1, ...stats.material_usage.map((m) => m.grams));

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.models")} value={stats.models.total} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.favorites")} value={stats.models.favorites} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.archived")} value={stats.models.archived} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.drafts")} value={stats.models.drafts} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.files")} value={stats.files.total} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.storageUsed")} value={humanizeBytes(stats.files.bytes_total)} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.tags")} value={stats.tags} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.followed")} value={stats.collections} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label={t("histStats.runs14")} value={runCounts.reduce((a, b) => a + b, 0)}>
            <Sparkline values={runCounts} aria-label={t("histStats.runs14Aria")} />
          </StatTile>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("histStats.prints")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <p className="text-xl font-semibold tabular-nums">{stats.prints.total}</p>
                <p className="text-xs text-muted-foreground">{t("histStats.logged")}</p>
              </div>
              <div>
                <p className="text-xl font-semibold tabular-nums text-success">
                  {stats.prints.succeeded}
                </p>
                <p className="text-xs text-muted-foreground">{t("histStats.succeeded")}</p>
              </div>
              <div>
                <p className="text-xl font-semibold tabular-nums text-destructive">{stats.prints.failed}</p>
                <p className="text-xs text-muted-foreground">{t("histStats.failed")}</p>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("histStats.filamentTime", {
                g: Math.round(stats.prints.filament_g_total),
                h: Math.round(stats.prints.duration_s_total / 3600),
              })}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("histStats.recent7")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              <span className="font-semibold tabular-nums">{stats.recent.models_added_7d}</span>{" "}
              <span className="text-muted-foreground">{t("histStats.modelsAdded")}</span>
            </p>
            <p>
              <span className="font-semibold tabular-nums">{stats.recent.prints_7d}</span>{" "}
              <span className="text-muted-foreground">{t("histStats.printsLogged")}</span>
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("histStats.filesByFormat")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {formatEntries.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("histStats.noFiles")}</p>
            ) : (
              formatEntries.map(([format, count]) => (
                <Bar key={format} label={format} value={count} max={maxFormatCount} />
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("histStats.storageByBackend")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {backendEntries.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("histStats.noFiles")}</p>
            ) : (
              backendEntries.map(([name, bytes]) => (
                <Bar key={name} label={`${name} — ${humanizeBytes(bytes)}`} value={bytes} max={maxBackendBytes} />
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("histStats.recentModels")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {stats.recent_models.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("histStats.noModels")}</p>
            ) : (
              stats.recent_models.slice(0, 10).map((model) => {
                const Icon = formatIcon(model.formats[0]);
                return (
                  <Link
                    key={model.id}
                    to="/models/$slug"
                    params={{ slug: model.slug }}
                    className="flex items-center gap-3 rounded-md hover:bg-accent/50"
                  >
                    {model.cover ? (
                      <img src={model.cover} alt="" className="size-10 shrink-0 rounded object-cover" />
                    ) : (
                      <div className="flex size-10 shrink-0 items-center justify-center rounded bg-muted text-muted-foreground">
                        <Icon className="size-5" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">{model.name}</p>
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {formatDate(model.updated_at)}
                    </span>
                  </Link>
                );
              })
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("histStats.recentPrints")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {stats.recent_prints.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("histStats.noPrints")}</p>
            ) : (
              stats.recent_prints.slice(0, 10).map((entry) => {
                const badge = RESULT_BADGE[entry.result];
                return (
                  <div key={entry.id} className="flex flex-wrap items-center gap-2 text-sm">
                    {entry.model_slug ? (
                      <Link
                        to="/models/$slug"
                        params={{ slug: entry.model_slug }}
                        className="truncate font-medium text-foreground hover:underline"
                      >
                        {entry.model_name}
                      </Link>
                    ) : (
                      <span className="truncate font-medium text-foreground">{entry.model_name}</span>
                    )}
                    <Badge variant={badge.variant} className={badge.className}>
                      {t(RESULT_LABELS[entry.result])}
                    </Badge>
                    {entry.material && (
                      <FilamentChip
                        size="sm"
                        color={entry.material.color ?? undefined}
                        material={entry.material.name}
                      />
                    )}
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                      {formatDate(entry.printed_at)}
                    </span>
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("histStats.materialUsage")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {stats.material_usage.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("histStats.noMaterial")}</p>
          ) : (
            stats.material_usage.map((entry) => (
              <Bar
                key={entry.material_id ?? `free:${entry.name}`}
                label={t("histStats.materialRow", {
                  name: entry.name,
                  g: Math.round(entry.grams),
                  prints: entry.prints,
                })}
                value={entry.grams}
                max={maxMaterialGrams}
              />
            ))
          )}
        </CardContent>
      </Card>

      <FilamentUsageSection />

      <Card>
        <CardHeader>
          <CardTitle>{t("histStats.printerJobs")}</CardTitle>
          <CardDescription>
            <Link to="/system/jobs" className="underline">
              {t("histStats.viewAllJobs")}
            </Link>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div>
              <p className="text-xl font-semibold tabular-nums">{stats.jobs.running}</p>
              <p className="text-xs text-muted-foreground">{t("histStats.running")}</p>
            </div>
            <div>
              <p className="text-xl font-semibold tabular-nums">{stats.jobs.queued}</p>
              <p className="text-xs text-muted-foreground">{t("histStats.queued")}</p>
            </div>
            <div>
              <p className="text-xl font-semibold tabular-nums text-destructive">{stats.jobs.failed_24h}</p>
              <p className="text-xs text-muted-foreground">{t("histStats.failed24")}</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
