/**
 * The dashboard's cross-printer recent-runs rail widget (P3 Task 8): a
 * compact echo of each archived run -- a result-tone dot, name, printer,
 * grams, and cost -- with inline Good/Reject grading (same `usePatchRun`
 * mutation `RunDetailSheet`'s `Grading` uses) for whoever holds
 * `library:write`. Clicking a row opens the full `RunDetailSheet`.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { usePatchRun } from "@/api/archives";
import { useAuth } from "@/api/auth";
import type { ArchiveOut } from "@/api/types";
import { HistoryIcon } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { CompareBar } from "@/components/print/ArchiveCompareDialog";
import { useRunSelection } from "@/components/print/useRunSelection";
import { RunCountBadge } from "@/components/print/ArchiveRuns";
import { RunDetailSheet } from "@/components/print/RunDetailSheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { StateBadge } from "@/components/ui/state-badge";
import { formatCost } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { totalGrams } from "@/lib/totalGrams";
import { toneForResult } from "@/lib/printerState";

function RunRow({
  run,
  onOpen,
  selected,
  onToggle,
}: {
  run: ArchiveOut;
  onOpen: (id: number) => void;
  selected: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const patch = usePatchRun();
  const { can } = useAuth();
  const grams = totalGrams(run);
  const name = run.model_name ?? run.subtask_name ?? t("runDetail.runId", { id: run.id });
  const canGrade = run.run != null && can("library:write");

  return (
    <li className="flex items-start gap-2 py-1.5">
      <Checkbox
        className="mt-1"
        checked={selected}
        onCheckedChange={onToggle}
        aria-label={t("archiveX.compare.select", { name })}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => onOpen(run.id)}>
            <StateBadge tone={toneForResult(run.result)} label="" aria-hidden="true" className="shrink-0" />
            <span className="min-w-0 flex-1 truncate text-sm">{name}</span>
            <RunCountBadge count={run.run_count} />
          </button>
          {canGrade && run.run ? (
            <div className="flex shrink-0 gap-1">
              <Button
                size="sm"
                variant={run.run.quality === "good" ? "secondary" : "outline"}
                className={run.run.quality === "good" ? "border-state-finished text-state-finished" : undefined}
                onClick={() => patch.mutate({ id: run.run!.id, patch: { quality: "good" } })}
              >
                {t("runDetail.good")}
              </Button>
              <Button
                size="sm"
                variant={run.run.quality === "reject" ? "destructive" : "outline"}
                onClick={() => patch.mutate({ id: run.run!.id, patch: { quality: "reject" } })}
              >
                {t("runDetail.reject")}
              </Button>
            </div>
          ) : null}
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
          <span className="min-w-0 truncate">{run.printer_name}</span>
          <span aria-hidden="true">·</span>
          <span className="font-mono tabular-nums">{grams != null ? `${grams}g` : "—"}</span>
          <span aria-hidden="true">·</span>
          <span className="font-mono tabular-nums">{formatCost(run.run?.total_cost)}</span>
        </div>
      </div>
    </li>
  );
}

export function RecentRunsList({ runs, limit = 10 }: { runs: ArchiveOut[]; limit?: number }) {
  const { t } = useTranslation();
  const [openId, setOpenId] = useState<number | null>(null);
  const shown = runs.slice(0, limit);
  const selection = useRunSelection();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("recentRuns.title")}</CardTitle>
        <CompareBar selection={selection} />
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {shown.length === 0 ? (
          <EmptyState icon={HistoryIcon} title={t("dash.empty.runs")} />
        ) : (
          <ul className="divide-y divide-border">
            {shown.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                onOpen={setOpenId}
                selected={selection.ids.includes(run.id)}
                onToggle={() => selection.toggle(run.id)}
              />
            ))}
          </ul>
        )}
        <RunDetailSheet archiveId={openId} onOpenChange={(open) => !open && setOpenId(null)} />
      </CardContent>
    </Card>
  );
}
