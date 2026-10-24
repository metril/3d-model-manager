/**
 * Print history: archived runs (`GET /archives`) with printer/result filters.
 * A row opens `RunDetailSheet`; the list polls while any run is still
 * pulling artifacts off the printer (`archivesRefetchInterval`).
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { archiveThumbnailUrl, useArchives } from "@/api/archives";
import { ApiError } from "@/api/client";
import { usePrinters } from "@/api/printers";
import type { ArchiveOut, ArchiveResult } from "@/api/types";
import { CompareBar } from "@/components/print/ArchiveCompareDialog";
import { useRunSelection } from "@/components/print/useRunSelection";
import { RunDetailSheet } from "@/components/print/RunDetailSheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatCost, formatDateTime, humanizeDuration } from "@/lib/format";

const RESULTS: ArchiveResult[] = ["completed", "failed", "canceled", "stopped"];
const ALL = "all";

function RunRow({
  run,
  onOpen,
  selected,
  onToggle,
}: {
  run: ArchiveOut;
  onOpen: () => void;
  selected: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const name = run.model_name ?? run.subtask_name ?? `Run #${run.id}`;
  return (
    <TableRow className="cursor-pointer" onClick={onOpen}>
      <TableCell className="w-8" onClick={(e) => e.stopPropagation()}>
        <Checkbox
          checked={selected}
          onCheckedChange={onToggle}
          aria-label={t("archiveX.compare.select", { name })}
        />
      </TableCell>
      <TableCell className="w-12">
        {run.has_thumbnail ? (
          <img src={archiveThumbnailUrl(run.id)} alt="" className="size-10 rounded object-cover" />
        ) : null}
      </TableCell>
      <TableCell className="max-w-64 truncate text-sm">{name}</TableCell>
      <TableCell>{run.printer_name}</TableCell>
      <TableCell>
        <Badge variant={run.result === "completed" ? "default" : "destructive"}>{run.result}</Badge>
      </TableCell>
      <TableCell className="font-mono tabular-nums">
        {run.duration_s != null ? humanizeDuration(run.duration_s) : "--"}
      </TableCell>
      <TableCell className="font-mono tabular-nums">
        {run.run?.filament_g != null ? `${run.run.filament_g} g` : "--"}
      </TableCell>
      <TableCell className="font-mono tabular-nums">{run.run ? formatCost(run.run.total_cost) : "--"}</TableCell>
      <TableCell>{run.run?.quality ?? ""}</TableCell>
      <TableCell>{formatDateTime(run.finished_at ?? run.created_at)}</TableCell>
    </TableRow>
  );
}

export function PrintJobHistory({
  printerId: fixedPrinterId,
  date,
  onClearDate,
}: { printerId?: number; date?: string; onClearDate?: () => void } = {}) {
  const { t } = useTranslation();
  const selection = useRunSelection();
  const [printerId, setPrinterId] = useState<number | undefined>();
  const [result, setResult] = useState<ArchiveResult | undefined>();
  const [openId, setOpenId] = useState<number | null>(null);
  const printers = usePrinters();
  const effectivePrinterId = fixedPrinterId ?? printerId;
  const archives = useArchives({ printerId: effectivePrinterId, result, ...(date ? { from: date, to: date } : {}) });
  const rows = archives.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Print history</CardTitle>
        <CardDescription>Finished runs, most recent first.</CardDescription>
        <div className="flex gap-2 pt-2">
          {fixedPrinterId == null ? (
            <Select
              aria-label="Printer filter"
              value={printerId != null ? String(printerId) : ALL}
              onValueChange={(v) => setPrinterId(v === ALL ? undefined : Number(v))}
            >
              <SelectTrigger aria-label="Printer filter" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All printers</SelectItem>
                {(printers.data ?? []).map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          <Select
            aria-label="Result filter"
            value={result ?? ALL}
            onValueChange={(v) => setResult(v === ALL ? undefined : (v as ArchiveResult))}
          >
            <SelectTrigger aria-label="Result filter" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All results</SelectItem>
              {RESULTS.map((r) => (
                <SelectItem key={r} value={r}>
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {date ? (
            <Button size="sm" variant="secondary" onClick={onClearDate}>
              {t("archiveX.calendar.clearDate", { date })}
            </Button>
          ) : null}
          <CompareBar selection={selection} />
        </div>
      </CardHeader>
      <CardContent>
        {archives.isLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : archives.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {archives.error instanceof ApiError ? archives.error.detail : "Couldn't load print history."}
          </p>
        ) : !rows || rows.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">No finished prints yet.</p>
        ) : (
          <Table className="data-table">
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead />
                <TableHead>Name</TableHead>
                <TableHead>Printer</TableHead>
                <TableHead>Result</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead>Grams</TableHead>
                <TableHead>Cost</TableHead>
                <TableHead>Quality</TableHead>
                <TableHead>Finished</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  onOpen={() => setOpenId(run.id)}
                  selected={selection.ids.includes(run.id)}
                  onToggle={() => selection.toggle(run.id)}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <RunDetailSheet archiveId={openId} onOpenChange={(open) => !open && setOpenId(null)} />
    </Card>
  );
}
