/**
 * Assign (or clear) the spool an AMS tray points at, from the dashboard/
 * detail chip itself (P5 Task 10). Only rendered around a chip when the
 * viewer holds `printers:control` for this printer -- the caller decides
 * that gate (`PrinterCard`/`PrinterStatusPanel`), not this component.
 *
 * `unit`/`tray` sent to `useAssignSlot` are the printer's own per-unit AMS
 * coordinates (`AmsTray.ams_unit`/`.tray`), NOT the global `.slot` id --
 * the external-spool sentinel is `ams_unit: 255, tray: 254`
 * (`app.services.spool_inventory`).
 */
import { useMemo, useState } from "react";

import { useAssignSlot, useSpools } from "@/api/spools";
import type { AmsTray, SpoolOut } from "@/api/types";
import { SpoolForm, type SpoolFormPrefill } from "@/components/inventory/SpoolForm";
import { Button } from "@/components/ui/button";
import { normalizeHex } from "@/components/ui/filament-chip";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { deltaE } from "@/lib/color";

const EXTERNAL_UNIT = 255;
const EXTERNAL_TRAY = 254;

function sortedCandidates(spools: SpoolOut[], tray: AmsTray): SpoolOut[] {
  const trayType = tray.tray_type ?? tray.material ?? undefined;
  const trayColor = normalizeHex(tray.color);
  return [...spools].sort((a, b) => {
    const aSame = trayType && a.material_type === trayType ? 0 : 1;
    const bSame = trayType && b.material_type === trayType ? 0 : 1;
    if (aSame !== bSame) return aSame - bSame;
    const aDist = trayColor && a.color_hex ? deltaE(trayColor, a.color_hex) : Infinity;
    const bDist = trayColor && b.color_hex ? deltaE(trayColor, b.color_hex) : Infinity;
    return aDist - bDist;
  });
}

export function SlotAssignPopover({
  printerId,
  tray,
  children,
}: {
  printerId: number;
  tray: AmsTray;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [newSpoolOpen, setNewSpoolOpen] = useState(false);
  const spoolsQuery = useSpools({ archived: false });
  const assignSlot = useAssignSlot(printerId);

  const unit = tray.ams_unit ?? EXTERNAL_UNIT;
  const trayIdx = tray.tray ?? EXTERNAL_TRAY;

  const candidates = useMemo(
    () => sortedCandidates(spoolsQuery.data ?? [], tray),
    [spoolsQuery.data, tray],
  );

  function assign(spoolId: number | null) {
    assignSlot.mutate({ unit, slot: trayIdx, payload: { spool_id: spoolId } }, { onSuccess: () => setOpen(false) });
  }

  const prefill: SpoolFormPrefill = {
    material_type: tray.tray_type ?? tray.material ?? undefined,
    color_hex: normalizeHex(tray.color),
    tray_uuid: tray.tray_uuid,
  };

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>{children}</PopoverTrigger>
        <PopoverContent>
          <PopoverHeader>
            <PopoverTitle>
              {tray.tray_type ?? tray.material ?? "Empty slot"}
              {tray.color ? ` · ${tray.color}` : ""}
            </PopoverTitle>
            <PopoverDescription>
              {tray.remain_pct != null ? `${tray.remain_pct}% remaining` : "No remaining estimate"}
              {tray.tray_uuid ? ` · RFID ${tray.tray_uuid}` : ""}
            </PopoverDescription>
          </PopoverHeader>

          <div className="flex flex-col gap-1.5">
            <Select
              aria-label="Assign spool"
              value={tray.spool ? String(tray.spool.id) : "none"}
              onValueChange={(v) => assign(v === "none" ? null : Number(v))}
            >
              <SelectTrigger aria-label="Assign spool" className="w-full">
                <SelectValue placeholder="Assign a spool" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">— Unassigned —</SelectItem>
                {candidates.map((spool) => (
                  <SelectItem key={spool.id} value={String(spool.id)}>
                    {spool.material_type}
                    {spool.color_name ? ` · ${spool.color_name}` : ""}
                    {spool.brand ? ` (${spool.brand})` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Button type="button" size="sm" variant="outline" disabled={assignSlot.isPending} onClick={() => assign(null)}>
              Clear
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setNewSpoolOpen(true)}>
              New spool from this tray
            </Button>
          </div>
        </PopoverContent>
      </Popover>
      <SpoolForm
        open={newSpoolOpen}
        onOpenChange={setNewSpoolOpen}
        prefill={prefill}
        onSaved={(spool) => {
          setNewSpoolOpen(false);
          assign(spool.id);
        }}
      />
    </>
  );
}
