/**
 * The dashboard's cross-printer recent-runs rail widget (P3 Task 8): a
 * compact echo of each archived run -- a result-tone dot, name, printer,
 * grams, and cost -- with inline Good/Reject grading (same `usePatchRun`
 * mutation `RunDetailSheet`'s `Grading` uses) for whoever holds
 * `library:write`. Clicking a row opens the full `RunDetailSheet`.
 */
import { useState } from "react";

import { usePatchRun } from "@/api/archives";
import { useAuth } from "@/api/auth";
import type { ArchiveOut } from "@/api/types";
import { RunDetailSheet } from "@/components/print/RunDetailSheet";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/ui/state-badge";
import { formatCost } from "@/lib/format";
import { toneForResult } from "@/lib/printerState";

/** Total filament grams for a run: sum of per-tray estimates when any are
 * present, else the graded run's actual `filament_g` -- mirrors the
 * priority a user would read off the run detail sheet. */
export function totalGrams(run: ArchiveOut): number | null {
  const est = run.est_filament ?? [];
  const sum = est.reduce((acc, e) => acc + (e.grams ?? 0), 0);
  if (est.length > 0 && sum > 0) return sum;
  return run.run?.filament_g ?? null;
}

function RunRow({ run, onOpen }: { run: ArchiveOut; onOpen: (id: number) => void }) {
  const patch = usePatchRun();
  const { can } = useAuth();
  const grams = totalGrams(run);
  const name = run.model_name ?? run.subtask_name ?? `Run #${run.id}`;
  const canGrade = run.run != null && can("library:write");

  return (
    <li className="flex items-center gap-2 py-1.5">
      <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => onOpen(run.id)}>
        <StateBadge tone={toneForResult(run.result)} label="" aria-hidden="true" className="shrink-0" />
        <span className="min-w-0 flex-1 truncate text-sm">{name}</span>
      </button>
      <span className="shrink-0 text-xs text-muted-foreground">{run.printer_name}</span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
        {grams != null ? `${grams}g` : "—"}
      </span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
        {formatCost(run.run?.total_cost)}
      </span>
      {canGrade && run.run ? (
        <div className="flex shrink-0 gap-1">
          <Button
            size="sm"
            variant={run.run.quality === "good" ? "default" : "outline"}
            onClick={() => patch.mutate({ id: run.run!.id, patch: { quality: "good" } })}
          >
            Good
          </Button>
          <Button
            size="sm"
            variant={run.run.quality === "reject" ? "destructive" : "outline"}
            onClick={() => patch.mutate({ id: run.run!.id, patch: { quality: "reject" } })}
          >
            Reject
          </Button>
        </div>
      ) : null}
    </li>
  );
}

export function RecentRunsList({ runs, limit = 10 }: { runs: ArchiveOut[]; limit?: number }) {
  const [openId, setOpenId] = useState<number | null>(null);
  const shown = runs.slice(0, limit);

  return (
    <div className="flex flex-col gap-2">
      <span className="label-caps">Recent runs</span>
      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No runs yet.</p>
      ) : (
        <ul className="divide-y divide-border">
          {shown.map((run) => (
            <RunRow key={run.id} run={run} onOpen={setOpenId} />
          ))}
        </ul>
      )}
      <RunDetailSheet archiveId={openId} onOpenChange={(open) => !open && setOpenId(null)} />
    </div>
  );
}
