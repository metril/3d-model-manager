/**
 * Expanded queue-row detail: what the slice produced (time, grams, per-plate
 * breakdown), the last error with a "View log" dialog when the sidecar
 * returned output, and Cancel while the entry is slicing.
 */
import { useState } from "react";

import { useCancelQueueSlice } from "@/api/queue";
import type { QueueEntry } from "@/api/types";
import { SliceLogDialog } from "@/components/slice/SliceLogDialog";
import { Button } from "@/components/ui/button";
import { humanizeDuration } from "@/lib/format";

export function QueueEntryDetail({ entry }: { entry: QueueEntry }) {
  const cancel = useCancelQueueSlice();
  const [logOpen, setLogOpen] = useState(false);
  const meta = entry.sliced_meta;
  const slicing = entry.state === "slicing";

  return (
    <div className="mt-2 space-y-2 rounded-md bg-muted/50 p-3 text-xs" data-testid="queue-entry-detail">
      {meta ? (
        <div className="space-y-1">
          <p className="font-medium">
            {meta.file_name ?? "Sliced file"}
            <span className="font-normal text-muted-foreground">
              {meta.print_time_s != null ? ` · ${humanizeDuration(meta.print_time_s)}` : ""}
              {meta.filament_g != null ? ` · ${Math.round(meta.filament_g)} g` : ""}
              {meta.plate_count ? ` · ${meta.plate_count} plate${meta.plate_count === 1 ? "" : "s"}` : ""}
            </span>
          </p>
          {meta.plates.length > 1 ? (
            <ul className="text-muted-foreground">
              {meta.plates.map((p) => (
                <li key={p.index}>
                  Plate {p.index}
                  {p.prediction_s != null ? ` · ${humanizeDuration(p.prediction_s)}` : ""}
                  {p.weight_g != null ? ` · ${Math.round(p.weight_g)} g` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <p className="text-muted-foreground">
          {entry.sliced_file_id != null ? "Sliced file details are still being extracted." : "Not sliced yet."}
        </p>
      )}
      {entry.last_error ? (
        <p className="text-destructive">
          {entry.last_error}
          {(entry.attempts ?? 0) > 0 ? ` (${entry.attempts}/3 attempts)` : ""}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {entry.last_log ? (
          <Button type="button" size="sm" variant="outline" onClick={() => setLogOpen(true)}>
            View log
          </Button>
        ) : null}
        {slicing ? (
          <Button type="button" size="sm" variant="outline" disabled={cancel.isPending} onClick={() => cancel.mutate(entry.id)}>
            Cancel slicing
          </Button>
        ) : null}
      </div>
      <SliceLogDialog open={logOpen} onOpenChange={setLogOpen} error={entry.last_error} log={entry.last_log} />
    </div>
  );
}
