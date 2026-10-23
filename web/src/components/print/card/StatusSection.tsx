/**
 * Print status block: 96 px thumbnail, stage text, print name, progress,
 * remaining / ETA, layer n/total, started-by and the skip-objects button
 * (badge = skippable objects). Idle shows "Last: ..." from the latest run
 * and a plate-clear pill after a finished print.
 */
import { ListXIcon, PrinterIcon } from "lucide-react";
import { useState } from "react";

import { usePrinterObjects } from "@/api/printerControl";
import type { ActiveJobOut, ArchiveOut, PrinterStatusOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatEta } from "@/lib/formatEta";
import { toneForGcode } from "@/lib/printerState";

import { SkipObjectsDialog } from "./SkipObjectsDialog";

export function etaClock(minutes: number | null | undefined, now = new Date()): string | null {
  if (minutes == null || !Number.isFinite(minutes) || minutes < 0) return null;
  const end = new Date(now.getTime() + minutes * 60_000);
  const sameDay = end.toDateString() === now.toDateString();
  const hm = end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? hm : `${end.toLocaleDateString([], { weekday: "short" })} ${hm}`;
}

function Thumb({ archiveId }: { archiveId: number | null }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="pc-thumb flex shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted">
      {archiveId != null && !failed ? (
        <img
          src={`/api/archives/${archiveId}/thumbnail`}
          alt=""
          className="size-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : (
        <PrinterIcon className="size-6 text-muted-foreground/50" />
      )}
    </div>
  );
}

export function StatusSection({
  printerId,
  status,
  activeJob,
  lastRuns,
  canControl,
}: {
  printerId: number;
  status: PrinterStatusOut;
  activeJob: ActiveJobOut | null;
  lastRuns: ArchiveOut[];
  canControl: boolean;
}) {
  const { tone } = toneForGcode(status.online, status.gcode_state);
  const active = tone === "printing" || tone === "paused";
  const [skipOpen, setSkipOpen] = useState(false);
  const objects = usePrinterObjects(printerId, active && canControl);
  const skippable = objects.data?.objects ?? [];
  const last = lastRuns[0];

  if (!status.online) {
    return (
      <p className="pc-t12 text-muted-foreground" data-slot="status-section">
        Offline{last ? ` — last: ${last.subtask_name ?? last.model_name ?? "print"}` : ""}
      </p>
    );
  }

  if (!active) {
    return (
      <div className="flex items-center gap-3" data-slot="status-section">
        <Thumb archiveId={last?.has_thumbnail ? last.id : null} />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="pc-t12 font-medium">{status.stage && status.stage !== "Idle" ? status.stage : "Idle"}</p>
          {last ? (
            <p className="pc-t11 truncate text-muted-foreground">
              Last: {last.subtask_name ?? last.model_name ?? "print"} · {last.result}
            </p>
          ) : (
            <p className="pc-t11 text-muted-foreground">Nothing printed yet</p>
          )}
          {status.gcode_state === "FINISH" ? <Badge variant="secondary">Clear plate</Badge> : null}
        </div>
      </div>
    );
  }

  const pct = Math.max(0, Math.min(100, status.mc_percent ?? 0));
  const remaining = formatEta(status.mc_remaining_time);
  const clock = etaClock(status.mc_remaining_time);
  const name = activeJob?.model_name ?? activeJob?.subtask_name ?? status.subtask_name ?? "Print";
  const thumbArchive = lastRuns.find((r) => activeJob && r.print_job_id === activeJob.id && r.has_thumbnail);
  return (
    <div className="flex gap-3" data-slot="status-section">
      <Thumb archiveId={thumbArchive?.id ?? null} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <p className="pc-t12 min-w-0 flex-1 truncate font-medium" title={name}>
            {name}
          </p>
          {skippable.length > 1 ? (
            <Button
              type="button"
              variant="outline"
              size="icon-xs"
              className="relative"
              aria-label="Skip objects"
              onClick={() => setSkipOpen(true)}
            >
              <ListXIcon />
              <span className="absolute -right-1.5 -top-1.5 rounded-full bg-primary px-1 text-[9px] leading-4 text-primary-foreground">
                {skippable.length}
              </span>
            </Button>
          ) : null}
        </div>
        {status.stage ? <p className="pc-t11 text-muted-foreground">{status.stage}</p> : null}
        <div
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full rounded-full bg-state-printing transition-all" style={{ width: `${pct}%` }} />
        </div>
        <div className="pc-t11 flex flex-wrap items-center justify-between gap-x-3 font-mono tabular-nums text-muted-foreground">
          <span>{pct}%</span>
          {remaining ? <span>{remaining} left</span> : null}
          {clock ? <span>ETA {clock}</span> : null}
          <span>
            Layer {status.layer_num ?? 0}/{status.total_layer_num ?? 0}
          </span>
        </div>
        {activeJob ? (
          <p className="pc-t10 text-muted-foreground">
            Started {activeJob.origin === "app" ? "from the app" : "on the printer"}
          </p>
        ) : null}
      </div>
      {skippable.length > 1 ? (
        <SkipObjectsDialog printerId={printerId} objects={skippable} open={skipOpen} onOpenChange={setSkipOpen} />
      ) : null}
    </div>
  );
}
