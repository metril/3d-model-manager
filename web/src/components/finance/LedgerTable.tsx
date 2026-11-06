/**
 * A cost center's ledger (P7 review/billing plan Task 8): kind chip,
 * signed amount, run/queue links, note, by, at.
 */
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useLedger } from "@/api/finance";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatCost, formatDateTime } from "@/lib/format";


export function LedgerTable({ costCenterId }: { costCenterId: number | null }) {
  const { t } = useTranslation();
  const ledger = useLedger(costCenterId);

  if (costCenterId == null) return null;
  if (ledger.isLoading) return <Skeleton className="h-32 w-full rounded-lg" />;
  if (ledger.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {ledger.error instanceof ApiError ? ledger.error.detail : t("finance.ledgerError")}
      </p>
    );
  }

  const entries = ledger.data ?? [];
  if (entries.length === 0) {
    return <p className="py-4 text-center text-sm text-muted-foreground">{t("finance.ledgerEmpty")}</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("finance.col.kind")}</TableHead>
          <TableHead>{t("finance.col.amount")}</TableHead>
          <TableHead>{t("finance.col.runQueue")}</TableHead>
          <TableHead>{t("finance.col.note")}</TableHead>
          <TableHead>{t("finance.col.by")}</TableHead>
          <TableHead>{t("finance.col.at")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map((entry) => {
          // The stored amount is already signed (reservations/charges are
          // negative, deposits/releases positive; adjustments go either way).
          const credit = entry.amount > 0;
          const signed = `${credit ? "+" : entry.amount < 0 ? "-" : ""}${formatCost(Math.abs(entry.amount))}`;
          return (
            <TableRow key={entry.id}>
              <TableCell>
                <Badge variant="outline">{t(`finance.kind.${entry.kind}`)}</Badge>
              </TableCell>
              <TableCell className={credit ? "font-mono tabular-nums text-state-finished" : "font-mono tabular-nums"}>
                {signed}
              </TableCell>
              <TableCell>
                {entry.print_queue_id != null ? (
                  <Link to="/print/queue" search={{ entry: entry.print_queue_id }} className="text-xs underline">
                    {t("finance.queueN", { id: entry.print_queue_id })}
                  </Link>
                ) : entry.print_id != null ? (
                  <span className="text-xs text-muted-foreground">{t("finance.runN", { id: entry.print_id })}</span>
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{entry.note ?? "—"}</TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {entry.created_by_username ?? (entry.created_by_user_id != null ? `#${entry.created_by_user_id}` : "—")}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{formatDateTime(entry.created_at)}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
