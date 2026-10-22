/**
 * Daily run-count series for the history page's "Runs (14d)" sparkline
 * (P3 Task 9): buckets archived runs by LOCAL calendar day over a trailing
 * window ending today, oldest day first so it feeds `Sparkline` left to
 * right in chronological order.
 */
import type { ArchiveOut } from "@/api/types";

function startOfLocalDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Count of archives per local day, oldest first, for the `days`-day
 * window ending on `now`'s local day (inclusive). */
export function dailyRunCounts(archives: ArchiveOut[], days = 14, now: Date = new Date()): number[] {
  const today = startOfLocalDay(now);
  const counts = Array<number>(days).fill(0);
  const dayMs = 24 * 60 * 60 * 1000;
  const windowStart = today - (days - 1) * dayMs;

  for (const a of archives) {
    const created = startOfLocalDay(new Date(a.created_at));
    if (created < windowStart || created > today) continue;
    const idx = Math.round((created - windowStart) / dayMs);
    counts[idx] += 1;
  }

  return counts;
}
