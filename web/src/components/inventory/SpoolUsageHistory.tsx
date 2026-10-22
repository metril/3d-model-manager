/**
 * Spool detail's usage panel (P5 Task 9): a `Sparkline` of the remaining-
 * grams series `GET /spools/{id}` returns, a table of individual usage
 * rows (a run link to `RunDetailSheet` when the row has an `archive_id`),
 * and an "Add manual usage" form for topping up the ledger by hand.
 */
import { useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { useAddUsage } from "@/api/spools";
import type { SpoolDetailOut } from "@/api/types";
import { useAuth } from "@/api/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sparkline } from "@/components/ui/sparkline";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";

export function SpoolUsageHistory({
  spool,
  onOpenRun,
}: {
  spool: SpoolDetailOut;
  onOpenRun?: (archiveId: number) => void;
}) {
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const addUsage = useAddUsage(spool.id);
  const [grams, setGrams] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number(grams);
    if (!(value > 0)) return;
    addUsage.mutate({ grams: value }, { onSuccess: () => setGrams("") });
  }

  const values = spool.series.map((p) => p.remaining_g);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <Sparkline values={values} aria-label="Remaining filament over time" />
        <span className="font-mono text-xs tabular-nums text-muted-foreground">
          {Math.round(spool.remaining_g)} g remaining
        </span>
      </div>

      {spool.usage.length === 0 ? (
        <p className="text-sm text-muted-foreground">No usage logged yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Run</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Grams</TableHead>
              <TableHead>Source</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {spool.usage.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  {row.archive_id != null ? (
                    <button
                      type="button"
                      className="text-left text-primary hover:underline"
                      onClick={() => onOpenRun?.(row.archive_id!)}
                    >
                      {row.model_name ?? `Run #${row.archive_id}`}
                    </button>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-muted-foreground">{formatDateTime(row.created_at)}</TableCell>
                <TableCell className="font-mono tabular-nums">{row.grams} g</TableCell>
                <TableCell>
                  <Badge variant="outline">{row.source}</Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {canWrite ? (
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="usage-grams" className="text-xs text-muted-foreground">
              Add manual usage (g)
            </label>
            <Input
              id="usage-grams"
              type="number"
              value={grams}
              onChange={(e) => setGrams(e.target.value)}
              className="w-28"
              disabled={addUsage.isPending}
            />
          </div>
          <Button type="submit" size="sm" variant="outline" disabled={addUsage.isPending}>
            {addUsage.isPending ? "Adding..." : "Add"}
          </Button>
          {addUsage.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {addUsage.error instanceof ApiError ? addUsage.error.detail : "Could not log usage"}
            </p>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}
