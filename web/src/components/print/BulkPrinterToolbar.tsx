import { useState } from "react";
import { toast } from "sonner";

import { api } from "@/api/client";
import { controlErrorMessage } from "@/api/printerControl";
import type { PrinterOverviewItem } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { dashStatus } from "@/lib/printerDashboard";

type Action = "pause" | "stop";

/** Pause / stop the selected printers, one request each (only those it applies to). */
export function BulkPrinterToolbar({
  selected,
  onDone,
  onClear,
}: {
  selected: PrinterOverviewItem[];
  onDone: () => void;
  onClear: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const eligible = (action: Action) =>
    selected.filter((it) => {
      if (!it.can_control) return false;
      const st = dashStatus(it);
      return action === "pause" ? st === "printing" : st === "printing" || st === "paused";
    });

  async function run(action: Action) {
    const targets = eligible(action);
    setBusy(true);
    const results = await Promise.allSettled(targets.map((it) => api.post(`/printers/${it.printer.id}/${action}`)));
    setBusy(false);
    const failed = results.filter((r) => r.status === "rejected");
    const ok = results.length - failed.length;
    const verb = action === "pause" ? "Paused" : "Stopped";
    if (failed.length === 0) toast.success(`${verb} ${ok} printer${ok === 1 ? "" : "s"}`);
    else toast.error(`${verb} ${ok} of ${results.length}: ${controlErrorMessage((failed[0] as PromiseRejectedResult).reason)}`);
    onDone();
  }

  const pauseN = eligible("pause").length;
  const stopN = eligible("stop").length;
  return (
    <div data-slot="bulk-toolbar" className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm">
      <span className="font-medium">{selected.length} selected</span>
      <ConfirmDialog
        trigger={
          <Button type="button" size="sm" variant="outline" disabled={busy || pauseN === 0}>
            Pause selected ({pauseN})
          </Button>
        }
        title={`Pause ${pauseN} print${pauseN === 1 ? "" : "s"}?`}
        confirmLabel="Pause"
        onConfirm={() => void run("pause")}
      />
      <ConfirmDialog
        trigger={
          <Button type="button" size="sm" variant="destructive" disabled={busy || stopN === 0}>
            Stop selected ({stopN})
          </Button>
        }
        title={`Stop ${stopN} print${stopN === 1 ? "" : "s"}?`}
        description="The printers will cancel their jobs in progress. This can't be undone."
        confirmLabel="Stop"
        destructive
        onConfirm={() => void run("stop")}
      />
      <Button type="button" size="sm" variant="ghost" className="ml-auto" onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}
