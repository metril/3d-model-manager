import type { ArchiveOut } from "@/api/types";

/** Total filament grams for a run: sum of per-tray estimates when any are
 * present, else the graded run's actual `filament_g` -- mirrors the
 * priority a user would read off the run detail sheet. */
export function totalGrams(run: ArchiveOut): number | null {
  const est = run.est_filament ?? [];
  const sum = est.reduce((acc, e) => acc + (e.grams ?? 0), 0);
  if (est.length > 0 && sum > 0) return sum;
  return run.run?.filament_g ?? null;
}
