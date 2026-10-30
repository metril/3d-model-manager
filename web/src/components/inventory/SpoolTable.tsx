/**
 * Spool inventory table (P5 Task 9): search/material/low-stock/archived
 * filters over `useSpools`, a remaining-grams bar per row, a muted
 * low-stock badge, resolved printer/AMS slot (or "Ext" for the external
 * spool sentinel), and a row click that opens the detail `Sheet`
 * (`SpoolsPage` owns that sheet; this component only reports the click).
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { useBulkSpoolAction, useExportCsv, useLocations } from "@/api/spoolAdmin";
import { useSpools } from "@/api/spools";
import { BulkEditDialog } from "@/components/inventory/BulkEditDialog";
import { CsvImportDialog } from "@/components/inventory/CsvImportDialog";
import { LabelsDialog } from "@/components/inventory/LabelsDialog";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
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
const BULK_CAP = 500;
const LABEL_CAP = 300;
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
  const [locationFilter, setLocationFilter] = useState<string>(ALL);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const [labelsOpen, setLabelsOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const { t } = useTranslation();
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const locations = useLocations();
  const exportCsv = useExportCsv();
  const archiveAction = useBulkSpoolAction(showArchived ? "restore" : "archive");
  const deleteAction = useBulkSpoolAction("delete");

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

  const allSpools = spoolsQuery.data ?? [];
  const spools = locationFilter === ALL ? allSpools : allSpools.filter((sp) => String(sp.location_id ?? "") === locationFilter);
  const ids = spools.filter((sp) => selected.has(sp.id)).map((sp) => sp.id);
  const allChecked = spools.length > 0 && ids.length === spools.length;
  const someChecked = ids.length > 0 && !allChecked;

  function toggle(id: number, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  function toggleAll(on: boolean) {
    setSelected(on ? new Set(spools.map((sp) => sp.id)) : new Set());
  }
  const clear = () => setSelected(new Set());
  const overBulkCap = ids.length > BULK_CAP;
  const overLabelCap = ids.length > LABEL_CAP;
  function runArchive() {
    archiveAction.mutate(ids, {
      onSuccess: () => {
        toast.success(t(showArchived ? "spoolAdmin.toolbar.restored" : "spoolAdmin.toolbar.archived", { count: ids.length }));
        clear();
      },
    });
  }
  function runDelete() {
    deleteAction.mutate(ids, {
      onSuccess: () => {
        toast.success(t("spoolAdmin.toolbar.deleted", { count: ids.length }));
        clear();
      },
    });
  }

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
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="spool-location-filter">{t("spoolAdmin.f.location")}</Label>
          <Select aria-label={t("spoolAdmin.f.location")} value={locationFilter} onValueChange={setLocationFilter}>
            <SelectTrigger id="spool-location-filter" aria-label={t("spoolAdmin.f.location")} className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("spoolAdmin.toolbar.allLocations")}</SelectItem>
              {(locations.data ?? []).map((l) => (
                <SelectItem key={l.id} value={String(l.id)}>
                  {l.name}
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
        <div className="ml-auto flex items-center gap-2 pb-0.5">
          <Button type="button" size="sm" variant="outline" disabled={exportCsv.isPending} onClick={() => exportCsv.mutate()}>
            {t("spoolAdmin.toolbar.exportCsv")}
          </Button>
          {canWrite ? (
            <Button type="button" size="sm" variant="outline" onClick={() => setImportOpen(true)}>
              {t("spoolAdmin.toolbar.importCsv")}
            </Button>
          ) : null}
        </div>
      </div>

      {ids.length > 0 ? (
        <div
          role="toolbar"
          aria-label={t("spoolAdmin.toolbar.label")}
          className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2 shadow-sm"
        >
          <span className="text-sm font-medium">{t("spoolAdmin.toolbar.selected", { n: ids.length })}</span>
          {canWrite ? (
            <>
              <Button type="button" size="sm" variant="outline" disabled={overBulkCap} onClick={() => setBulkOpen(true)}>
                {t("spoolAdmin.toolbar.bulkEdit")}
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={archiveAction.isPending || overBulkCap} onClick={runArchive}>
                {showArchived ? t("spoolAdmin.toolbar.restore") : t("spoolAdmin.toolbar.archive")}
              </Button>
            </>
          ) : null}
          <Button type="button" size="sm" variant="outline" disabled={overLabelCap} onClick={() => setLabelsOpen(true)}>
            {t("spoolAdmin.toolbar.labels")}
          </Button>
          {canWrite ? (
            <Button type="button" size="sm" variant="destructive" disabled={overBulkCap} onClick={() => setDeleteOpen(true)}>
              {t("spoolAdmin.toolbar.delete")}
            </Button>
          ) : null}
          {overBulkCap || overLabelCap ? (
            <span className="text-xs text-muted-foreground">
              {overBulkCap ? t("spoolAdmin.toolbar.capBulk", { max: BULK_CAP }) : t("spoolAdmin.toolbar.capLabels", { max: LABEL_CAP })}
            </span>
          ) : null}
          <Button type="button" size="sm" variant="ghost" className="ml-auto" onClick={clear}>
            {t("spoolAdmin.toolbar.clear")}
          </Button>
        </div>
      ) : null}

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
              <TableHead className="w-8">
                <Checkbox
                  aria-label={t("spoolAdmin.toolbar.selectAll")}
                  checked={allChecked ? true : someChecked ? "indeterminate" : false}
                  onCheckedChange={(c) => toggleAll(c === true)}
                />
              </TableHead>
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
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    aria-label={t("spoolAdmin.toolbar.selectRow", { name: `${spool.material_type} ${spool.color_name ?? ""}`.trim() })}
                    checked={selected.has(spool.id)}
                    onCheckedChange={(c) => toggle(spool.id, c === true)}
                  />
                </TableCell>
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

      <BulkEditDialog ids={ids} open={bulkOpen} onOpenChange={setBulkOpen} onDone={clear} />
      <LabelsDialog ids={ids} open={labelsOpen} onOpenChange={setLabelsOpen} />
      <CsvImportDialog open={importOpen} onOpenChange={setImportOpen} />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t("spoolAdmin.toolbar.deleteTitle", { count: ids.length })}
        description={t("spoolAdmin.toolbar.deleteDesc")}
        confirmLabel={t("spoolAdmin.toolbar.delete")}
        destructive
        onConfirm={runDelete}
      />
    </div>
  );
}
