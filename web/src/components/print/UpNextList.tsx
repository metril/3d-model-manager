/**
 * The dashboard's queue-head rail widget (P3 Task 8): a compact echo of
 * `QueuePage`'s row -- up/down reorder (`useMoveQueueEntry`) and a
 * "Print…" action that opens the shared `PrintDialog` (P5 Task 11) --
 * only when the entry is sliced AND at least one printer is reachable for
 * this user. A footer link hands off to the full queue.
 *
 * P6 Task 10: an optional `printerId` renders the SAME widget scoped to
 * one printer's own `up_next` (`PrinterCard`/`PrinterDetailPage`, from
 * `PrinterOverviewItem.up_next`) rather than the dashboard's unassigned
 * head (`PrintersOverviewOut.up_next`) -- only the empty-state copy and a
 * `data-printer-id` marker differ; the rows themselves are identical.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { usePrinters } from "@/api/printers";
import { useMoveQueueEntry } from "@/api/queue";
import type { QueueEntry } from "@/api/types";
import { PrintDialog } from "@/components/print/PrintDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function UpNextRow({ entry }: { entry: QueueEntry }) {
  const moveEntry = useMoveQueueEntry();
  const { canPrinter } = useAuth();
  const printers = usePrinters();
  const printable = (printers.data ?? []).filter((p) => canPrinter(p.id, "print"));
  const [printOpen, setPrintOpen] = useState(false);
  // This list is only the queue's HEAD (`PrintersOverviewOut.up_next`), not
  // the whole queue, so there's no reliable "last" boundary to disable
  // Down against here -- only "Up" at the global position 1 is known for
  // certain. The full queue page (`QueuePage.tsx`) has both boundaries.
  const isFirst = entry.position <= 1;

  return (
    <li className="flex items-center gap-2 py-1.5">
      <div className="min-w-0 flex-1">
        <Link to="/models/$slug" params={{ slug: entry.model.slug }} className="truncate text-sm hover:underline">
          {entry.model.name}
        </Link>
      </div>
      {entry.printable_file && printable.length > 0 ? (
        <>
          <Button type="button" size="sm" variant="outline" onClick={() => setPrintOpen(true)}>
            Print…
          </Button>
          <PrintDialog
            open={printOpen}
            onOpenChange={setPrintOpen}
            source={{ kind: "model", modelId: entry.model_id }}
          />
        </>
      ) : null}
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Move ${entry.model.name} up`}
        disabled={isFirst || moveEntry.isPending}
        onClick={() => moveEntry.mutate({ entryId: entry.id, position: entry.position - 1 })}
      >
        <ArrowUpIcon className="size-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Move ${entry.model.name} down`}
        disabled={moveEntry.isPending}
        onClick={() => moveEntry.mutate({ entryId: entry.id, position: entry.position + 1 })}
      >
        <ArrowDownIcon className="size-4" />
      </Button>
    </li>
  );
}

export function UpNextList({
  entries,
  printerId,
  bare = false,
}: {
  entries: QueueEntry[];
  printerId?: number;
  /** Render without the Card surface, for placement inside another card. */
  bare?: boolean;
}) {
  const body = (
    <>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {printerId != null ? "Nothing queued for this printer." : "Nothing queued."}
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {entries.map((entry) => (
            <UpNextRow key={entry.id} entry={entry} />
          ))}
        </ul>
      )}
      <Link to="/print/queue" className="text-xs text-muted-foreground hover:underline">
        All queue
      </Link>
    </>
  );
  if (bare) {
    return (
      <div className="flex flex-col gap-2" data-printer-id={printerId}>
        <span className="label-caps">Up next</span>
        {body}
      </div>
    );
  }
  return (
    <Card data-printer-id={printerId}>
      <CardHeader>
        <CardTitle>Up next</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">{body}</CardContent>
    </Card>
  );
}
