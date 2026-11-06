/**
 * Query hook for the dashboard stats endpoint (R11-B item 13):
 * `GET /api/stats` -- cheap aggregates, cached server-side for 30s.
 */
import { queryOptions, useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { FilamentStatsOut, StatsOut } from "@/api/types";

export const statsQueryOptions = queryOptions({
  queryKey: ["stats"] as const,
  queryFn: () => api.get<StatsOut>("/stats"),
});

export function useStats() {
  return useQuery(statsQueryOptions);
}

export interface FilamentStatsFilters {
  days: number;
  bucket: "day" | "week" | "month";
}

/** `GET /stats/filament` (P5 Task 8/12): per-material and per-spool
 * filament usage over time -- the History page's filament stats section. */
export function useFilamentStats({ days, bucket }: FilamentStatsFilters) {
  return useQuery({
    queryKey: ["stats", "filament", days, bucket] as const,
    queryFn: () =>
      api.get<FilamentStatsOut>(
        `/stats/filament?days=${days}&bucket=${bucket}&tz=${encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone)}`,
      ),
  });
}

export interface SuccessRate {
  total: number;
  completed: number;
  failed: number;
  canceled: number;
  stopped: number;
  success_rate: number | null;
}
export interface TimeAccuracy {
  count: number;
  mean_delta_pct: number | null;
  mean_abs_delta_pct: number | null;
  distribution: { label: string; count: number }[];
}
export interface FailureGroup {
  key: string;
  name?: string | null;
  failed: number;
  total: number;
  failure_rate: number;
}
export interface FailureAnalysis {
  total_failed: number;
  by_error_code: { key: string; count: number }[];
  by_hms: { key: string; count: number }[];
  by_printer: FailureGroup[];
  by_material: FailureGroup[];
}
export interface RecordEntry {
  archive_id: number;
  name: string | null;
  value: number;
  printer_id: number;
}
export interface PrinterStat {
  printer_id: number;
  name: string;
  hours: number;
  jobs: number;
  completed: number;
  success_rate: number | null;
  filament_g: number;
}
export interface StatsDashboard {
  days: number;
  success_rate: SuccessRate;
  time_accuracy: TimeAccuracy;
  failures: FailureAnalysis;
  /** counts[weekday][hour]; weekday 0 = Monday, in the `tz` the request asked for (the browser zone). */
  heatmap: { counts: number[][]; max: number };
  records: {
    longest_print: RecordEntry | null;
    most_filament: RecordEntry | null;
    busiest_day: { date: string; prints: number } | null;
  };
  printers: PrinterStat[];
  filament_trends: { week: string; material: string; grams: number }[];
}

function browserTz(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/** `GET /stats/dashboard?days=` -- every Stats-page widget in one payload. */
export function useStatsDashboard(days: number) {
  // Bucket the heatmap in the viewer's zone, not UTC.
  const tz = browserTz();
  return useQuery({
    queryKey: ["stats", "dashboard", days, tz] as const,
    queryFn: () =>
      api.get<StatsDashboard>(`/stats/dashboard?days=${days}${tz ? `&tz=${encodeURIComponent(tz)}` : ""}`),
  });
}
