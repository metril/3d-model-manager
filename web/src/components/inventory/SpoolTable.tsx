/**
 * Spool inventory table (P5 Task 9): search/material/low-stock/archived
 * filters over `useSpools`, a remaining-grams bar per row, a muted
 * low-stock badge, resolved printer/AMS slot (or "Ext" for the external
 * spool sentinel), and a row click that opens the detail `Sheet`
 * (`SpoolsPage` owns that sheet; this component only reports the click).
 */
import { useMemo, useState } from "react";

import { useSpools } from "@/api/spools";
import { usePrinters } from "@/api/printers";
import { ApiError } from "@/api/client";
import type { MaterialKind, SpoolOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";

const EXTERNAL_UNIT = 255;
const ALL = "all";
const MATERIAL_KINDS: MaterialKind[] = ["PLA", "PETG", "ABS", "ASA", "TPU", "Resin", "Other"];

function slotLabel(spool: SpoolOut, printerName: (id: number) => string): string {
  if (!spool.slot) return "—";
  if (spool.slot.ams_unit === EXTERNAL_UNIT) return "Ext";
  return `${printerName(spool.slot.printer_id)} · AMS ${spool.slot.ams_unit + 1}-${spool.slot.slot + 1}`;
}

function RemainingBar({ spool }: { spool: SpoolOut }) {
  const remaining = Math.max(0, spool.remaining_g);
  const fraction = spool.label_weight_g > 0 ? Math.min(1, remaining / spool.label_weight_g) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded bg-muted">
        <div
          className="h-1.5 rounded bg-primary"
          style={{ width: `${fraction * 100}%` }}
        />
      </div>
      <span className="font-mono text-xs tabular-nums">{Math.round(remaining)} g</span>
      {spool.low_stock ? (
        <Badge variant="secondary" className="shrink-0">
          Low
        </Badge>
      ) : null}
    </div>
  );
}

export function SpoolTable({
  onOpen,
  initialLowStock = false,
}: {
  onOpen: (spool: SpoolOut) => void;
  initialLowStock?: boolean;
}) {
  const [q, setQ] = useState("");
  const [materialType, setMaterialType] = useState<string>(ALL);
  const [lowStockOnly, setLowStockOnly] = useState(initialLowStock);
  const [showArchived, setShowArchived] = useState(false);

  const spoolsQuery = useSpools({
    archived: showArchived,
    low_stock: lowStockOnly ? true : undefined,
    material_type: materialType === ALL ? undefined : materialType,
    q: q || undefined,
  });
  const printers = usePrinters();
  const printerName = useMemo(() => {
    const byId = new Map((printers.data ?? []).map((p) => [p.id, p.name]));
    return (id: number) => byId.get(id) ?? `Printer ${id}`;
  }, [printers.data]);

  const spools = spoolsQuery.data ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="spool-search">Search</Label>
          <Input
            id="spool-search"
            placeholder="Brand or color"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            className="w-48"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="spool-material-filter">Material</Label>
          <Select aria-label="Material" value={materialType} onValueChange={setMaterialType}>
            <SelectTrigger id="spool-material-filter" aria-label="Material" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All materials</SelectItem>
              {MATERIAL_KINDS.map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {kind}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <Checkbox checked={lowStockOnly} onCheckedChange={(c) => setLowStockOnly(c === true)} />
          Low stock
        </label>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <Checkbox checked={showArchived} onCheckedChange={(c) => setShowArchived(c === true)} />
          Show archived
        </label>
      </div>

      {spoolsQuery.isLoading ? (
        <Skeleton className="h-32 w-full rounded-lg" />
      ) : spoolsQuery.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {spoolsQuery.error instanceof ApiError ? spoolsQuery.error.detail : "Couldn't load spools."}
        </p>
      ) : spools.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">No spools match these filters.</p>
      ) : (
        <Table className="data-table">
          <TableHeader>
            <TableRow>
              <TableHead />
              <TableHead>Material</TableHead>
              <TableHead>Remaining</TableHead>
              <TableHead>Location</TableHead>
              <TableHead>Slot</TableHead>
              <TableHead>Last used</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {spools.map((spool) => (
              <TableRow key={spool.id} className="cursor-pointer" onClick={() => onOpen(spool)}>
                <TableCell>
                  <FilamentChip color={spool.color_hex ?? undefined} />
                </TableCell>
                <TableCell>
                  <div className="flex flex-col">
                    <span className="font-medium text-foreground">
                      {spool.material_type}
                      {spool.brand ? <span className="text-muted-foreground"> · {spool.brand}</span> : null}
                    </span>
                    {spool.color_name ? (
                      <span className="text-xs text-muted-foreground">{spool.color_name}</span>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <RemainingBar spool={spool} />
                    {spool.spoolman_id != null ? (
                      <Badge variant="outline" className="shrink-0" title={`Spoolman #${spool.spoolman_id}`}>
                        Spoolman
                      </Badge>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">{spool.location ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs">{slotLabel(spool, printerName)}</TableCell>
                <TableCell className="text-muted-foreground">{formatDateTime(spool.last_used_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
