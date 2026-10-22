/**
 * The active-job row inside a `PrinterCard` (P3 Task 7): name, plate, a
 * thin amber progress bar, layer/ETA telemetry, and Pause/Resume + Stop
 * controls gated on `canControl` (the overview's server-computed
 * `can_control`, equivalent to `canPrinter(id, "control")`). Stop confirms
 * via `ConfirmDialog` -- a stray tap shouldn't kill a multi-hour print.
 *
 * `job` carries `model_slug`/`model_name` (resolved off the job's file ->
 * model, P3 Task 1's `ActiveJobOut`) but no cover-image URL -- that's only
 * ever resolved server-side per model (`ModelSummary.cover`), not
 * constructible from a slug alone -- so the thumb is always the glyph
 * placeholder for now; a real thumbnail would need the overview to embed
 * one explicitly, which is out of scope here.
 */
import { PrinterIcon } from "lucide-react";

import { usePrinterCommand } from "@/api/printers";
import type { ActiveJobOut, PrinterStatusOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";

/** `mc_remaining_time` (minutes) -> `"1h 05m"` / `"5m"`. Zero-pads minutes
 * only once there's an hour component, so the detail-sheet style doesn't
 * misread a short remaining time as a typo (`"05m"` alone looks wrong). */
export function formatEta(minutes: number | null | undefined): string | null {
  if (minutes == null || !Number.isFinite(minutes) || minutes < 0) return null;
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  if (hours <= 0) return `${mins}m`;
  return `${hours}h ${String(mins).padStart(2, "0")}m`;
}

export function ActiveJobStrip({
  job,
  status,
  printerId,
  canControl,
}: {
  job: ActiveJobOut;
  status: PrinterStatusOut;
  printerId: number;
  canControl: boolean;
}) {
  const command = usePrinterCommand(printerId);
  const paused = status.gcode_state === "PAUSE";
  const percent = status.mc_percent ?? 0;
  const eta = formatEta(status.mc_remaining_time);
  const name = job.model_name ?? job.subtask_name ?? `Job #${job.id}`;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-[6px] border bg-muted">
          <PrinterIcon className="size-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{name}</p>
          {job.plate != null ? <p className="text-xs text-muted-foreground">Plate {job.plate}</p> : null}
        </div>
      </div>

      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-0.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-state-printing transition-all" style={{ width: `${percent}%` }} />
      </div>

      <div className="flex items-center justify-between font-mono text-xs tabular-nums text-muted-foreground">
        <span>Layer {status.layer_num ?? 0}/{status.total_layer_num ?? 0}</span>
        {eta ? <span>ETA {eta}</span> : null}
      </div>

      {canControl ? (
        <div className="flex gap-2">
          {paused ? (
            <Button size="sm" variant="outline" disabled={command.isPending} onClick={() => command.mutate("resume")}>
              Resume
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled={command.isPending} onClick={() => command.mutate("pause")}>
              Pause
            </Button>
          )}
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="destructive" disabled={command.isPending}>
                Stop
              </Button>
            }
            title="Stop this print?"
            description="The printer will cancel the job in progress. This can't be undone."
            confirmLabel="Confirm"
            destructive
            onConfirm={() => command.mutate("stop")}
          />
        </div>
      ) : null}
    </div>
  );
}
