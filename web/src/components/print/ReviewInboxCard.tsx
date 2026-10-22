/**
 * Dashboard rail card listing pending review items (P7 review/billing
 * plan Task 9): model, plate, printer or "Any", creator, est cost, with
 * Approve/Reject (`ReviewActions`). `PrintDashboardPage` only mounts this
 * once the viewer can review AND the count is > 0.
 */
import { usePrinters } from "@/api/printers";
import { useReviewInbox } from "@/api/queue";
import type { QueueEntry } from "@/api/types";
import { ReviewActions } from "@/components/print/ReviewActions";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCost } from "@/lib/format";

function ReviewRow({ entry, printerName }: { entry: QueueEntry; printerName: string }) {
  const name = entry.file_name ?? entry.model?.name ?? "—";
  return (
    <li className="space-y-1.5 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium">{name}</span>
        <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
          {formatCost(entry.reserved_cost)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        {entry.plate != null ? <span>Plate {entry.plate}</span> : null}
        <span>{printerName}</span>
      </div>
      <ReviewActions entryId={entry.id} />
    </li>
  );
}

export function ReviewInboxCard() {
  const inbox = useReviewInbox();
  const printers = usePrinters();
  const entries = inbox.data ?? [];

  function printerName(entry: QueueEntry): string {
    const id = entry.printer_id ?? entry.assigned_printer_id;
    if (id == null) return "Any";
    return (printers.data ?? []).find((p) => p.id === id)?.name ?? "Any";
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Review inbox</CardTitle>
      </CardHeader>
      <CardContent>
        {inbox.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : entries.length === 0 ? (
          <p className="py-2 text-center text-sm text-muted-foreground">Nothing pending.</p>
        ) : (
          <ul className="divide-y divide-border">
            {entries.map((entry) => (
              <ReviewRow key={entry.id} entry={entry} printerName={printerName(entry)} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
