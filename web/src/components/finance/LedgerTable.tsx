/**
 * A cost center's ledger (P7 review/billing plan Task 8): kind chip,
 * signed amount, run/queue links, note, by, at.
 */
import { Link } from "@tanstack/react-router";

import { ApiError } from "@/api/client";
import { useLedger } from "@/api/finance";
import type { LedgerKind } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatCost, formatDateTime } from "@/lib/format";

const KIND_LABELS: Record<LedgerKind, string> = {
  deposit: "Deposit",
  adjustment: "Adjustment",
  charge: "Charge",
  reservation: "Reservation",
  release: "Release",
};

// Credits (money coming in / released back) read positive-green,
// everything that reduces the balance reads as a plain negative amount --
// mirrors the queue's `QueueStateChip` tone vocabulary loosely without
// introducing a new color.
const CREDIT_KINDS: LedgerKind[] = ["deposit", "release"];

export function LedgerTable({ costCenterId }: { costCenterId: number | null }) {
  const ledger = useLedger(costCenterId);

  if (costCenterId == null) return null;
  if (ledger.isLoading) return <Skeleton className="h-32 w-full rounded-lg" />;
  if (ledger.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {ledger.error instanceof ApiError ? ledger.error.detail : "Couldn't load the ledger."}
      </p>
    );
  }

  const entries = ledger.data ?? [];
  if (entries.length === 0) {
    return <p className="py-4 text-center text-sm text-muted-foreground">No ledger entries yet.</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Kind</TableHead>
          <TableHead>Amount</TableHead>
          <TableHead>Run / queue</TableHead>
          <TableHead>Note</TableHead>
          <TableHead>By</TableHead>
          <TableHead>At</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map((entry) => {
          const credit = CREDIT_KINDS.includes(entry.kind);
          const signed = `${credit ? "+" : "-"}${formatCost(Math.abs(entry.amount))}`;
          return (
            <TableRow key={entry.id}>
              <TableCell>
                <Badge variant="outline">{KIND_LABELS[entry.kind]}</Badge>
              </TableCell>
              <TableCell className={credit ? "font-mono tabular-nums text-state-finished" : "font-mono tabular-nums"}>
                {signed}
              </TableCell>
              <TableCell>
                {entry.print_queue_id != null ? (
                  <Link to="/print/queue" className="text-xs underline">
                    Queue #{entry.print_queue_id}
                  </Link>
                ) : entry.print_id != null ? (
                  <span className="text-xs text-muted-foreground">Run #{entry.print_id}</span>
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{entry.note ?? "—"}</TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {entry.created_by_user_id ?? "—"}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{formatDateTime(entry.created_at)}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
