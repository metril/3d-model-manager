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
    queryFn: () => api.get<FilamentStatsOut>(`/stats/filament?days=${days}&bucket=${bucket}`),
  });
}
