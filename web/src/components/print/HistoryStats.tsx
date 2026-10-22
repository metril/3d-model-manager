/**
 * The history page's stats tab (P3 Task 9): the library-wide overview that
 * used to be `DashboardPage`/`DashboardContent`, rebuilt on the shared
 * `StatTile` primitive (P3 Task 4) and extended with a "Runs (14d)"
 * sparkline fed by `useArchives()` + `dailyRunCounts` -- the stats tab's
 * only printer-specific metric, so it keeps working even with the runs
 * tab hidden behind `printer_enabled=false`.
 */
import { Link } from "@tanstack/react-router";

import { useArchives } from "@/api/archives";
import { ApiError } from "@/api/client";
import { useStats } from "@/api/stats";
import type { PrintResult } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/ui/sparkline";
import { StatTile } from "@/components/ui/stat-tile";
import { formatDate, humanizeBytes } from "@/lib/format";
import { formatIcon } from "@/lib/formatMeta";
import { dailyRunCounts } from "@/lib/runSeries";

// Duplicated from `PrintsTab.tsx` (not exported there) -- keep in sync if the
// result vocabulary changes.
const RESULT_LABELS: Record<PrintResult, string> = {
  success: "Success",
  fail: "Fail",
  partial: "Partial",
};
const RESULT_BADGE: Record<PrintResult, { variant: "destructive" | "outline"; className?: string }> = {
  success: { variant: "outline", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" },
  fail: { variant: "destructive" },
  partial: { variant: "outline", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
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

export function HistoryStats() {
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
          <CardTitle>Couldn&apos;t load the dashboard</CardTitle>
          <CardDescription>
            {statsQuery.error instanceof ApiError ? statsQuery.error.detail : "Something went wrong."}
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
          <StatTile label="Models" value={stats.models.total} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Favorites" value={stats.models.favorites} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Archived" value={stats.models.archived} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Drafts" value={stats.models.drafts} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Files" value={stats.files.total} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Storage used" value={humanizeBytes(stats.files.bytes_total)} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Tags" value={stats.tags} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Followed collections" value={stats.collections} />
        </div>
        <div className="rounded-[6px] border p-3">
          <StatTile label="Runs (14d)" value={runCounts.reduce((a, b) => a + b, 0)}>
            <Sparkline values={runCounts} aria-label="Runs over the last 14 days" />
          </StatTile>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Prints</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <p className="text-xl font-semibold tabular-nums">{stats.prints.total}</p>
                <p className="text-xs text-muted-foreground">Logged</p>
              </div>
              <div>
                <p className="text-xl font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                  {stats.prints.succeeded}
                </p>
                <p className="text-xs text-muted-foreground">Succeeded</p>
              </div>
              <div>
                <p className="text-xl font-semibold tabular-nums text-destructive">{stats.prints.failed}</p>
                <p className="text-xs text-muted-foreground">Failed</p>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {Math.round(stats.prints.filament_g_total)} g filament ·{" "}
              {Math.round(stats.prints.duration_s_total / 3600)} h print time (all-time)
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent activity (7 days)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              <span className="font-semibold tabular-nums">{stats.recent.models_added_7d}</span>{" "}
              <span className="text-muted-foreground">models added</span>
            </p>
            <p>
              <span className="font-semibold tabular-nums">{stats.recent.prints_7d}</span>{" "}
              <span className="text-muted-foreground">prints logged</span>
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Files by format</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {formatEntries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No files yet.</p>
            ) : (
              formatEntries.map(([format, count]) => (
                <Bar key={format} label={format} value={count} max={maxFormatCount} />
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Storage by backend</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {backendEntries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No files yet.</p>
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
            <CardTitle>Recent models</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {stats.recent_models.length === 0 ? (
              <p className="text-sm text-muted-foreground">No models yet.</p>
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
            <CardTitle>Recent prints</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {stats.recent_prints.length === 0 ? (
              <p className="text-sm text-muted-foreground">No prints logged yet.</p>
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
                      {RESULT_LABELS[entry.result]}
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
          <CardTitle>Material usage</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {stats.material_usage.length === 0 ? (
            <p className="text-sm text-muted-foreground">No material usage logged yet.</p>
          ) : (
            stats.material_usage.map((entry) => (
              <Bar
                key={entry.material_id ?? `free:${entry.name}`}
                label={`${entry.name} — ${Math.round(entry.grams)} g · ${entry.prints} print(s)`}
                value={entry.grams}
                max={maxMaterialGrams}
              />
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Printer jobs</CardTitle>
          <CardDescription>
            <Link to="/system/jobs" className="underline">
              View all jobs
            </Link>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div>
              <p className="text-xl font-semibold tabular-nums">{stats.jobs.running}</p>
              <p className="text-xs text-muted-foreground">Running</p>
            </div>
            <div>
              <p className="text-xl font-semibold tabular-nums">{stats.jobs.queued}</p>
              <p className="text-xs text-muted-foreground">Queued</p>
            </div>
            <div>
              <p className="text-xl font-semibold tabular-nums text-destructive">{stats.jobs.failed_24h}</p>
              <p className="text-xs text-muted-foreground">Failed (24h)</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
