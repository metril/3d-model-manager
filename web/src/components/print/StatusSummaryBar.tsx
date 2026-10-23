import type { DashSummary } from "@/lib/printerDashboard";
import { formatEta } from "@/lib/formatEta";
import { DASH_STATUSES, type DashStatus } from "@/pages/printerDashSearch";
import { cn } from "@/lib/utils";

const TONE: Record<DashStatus, string> = {
  printing: "bg-state-printing",
  paused: "bg-state-paused",
  finished: "bg-state-finished",
  available: "bg-state-idle",
  problem: "bg-state-failed",
  offline: "bg-state-offline",
};

/** Fleet counts (click to filter) plus the next printer to free up. */
export function StatusSummaryBar({
  summary,
  active,
  onSelect,
}: {
  summary: DashSummary;
  active?: DashStatus;
  onSelect: (status: DashStatus | undefined) => void;
}) {
  return (
    <div data-slot="status-summary" className="flex flex-wrap items-center gap-2 text-sm">
      {DASH_STATUSES.map((st) => (
        <button
          key={st}
          type="button"
          aria-pressed={active === st}
          aria-label={`${st} ${summary.counts[st]}`}
          onClick={() => onSelect(active === st ? undefined : st)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 capitalize",
            active === st ? "border-primary bg-primary/10" : "hover:bg-muted",
          )}
        >
          <span className={cn("size-2 rounded-full", TONE[st])} />
          <span className="font-mono tabular-nums">{summary.counts[st]}</span>
          {st}
        </button>
      ))}
      {summary.next ? (
        <span className="ml-auto text-muted-foreground" data-testid="next-free">
          Next free: {summary.next.name} in {formatEta(summary.next.minutes)}
        </span>
      ) : null}
    </div>
  );
}
