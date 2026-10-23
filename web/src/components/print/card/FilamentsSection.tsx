/**
 * AMS unit cards (humidity / temperature, hover for serial + firmware),
 * slot circles (colour, fill bar, loaded marker, mapping pill, low-stock
 * flag) with a per-slot load/unload menu, the existing assign-spool popover
 * around each circle, the external spool card and an AMS-backup badge.
 */
import { DropletsIcon, MoreVerticalIcon, ThermometerIcon } from "lucide-react";

import { useControl } from "@/api/printerControl";
import type { AmsTray, AmsUnit, PrinterOut, PrinterStatusOut } from "@/api/types";
import { SlotAssignPopover } from "@/components/inventory/SlotAssignPopover";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { normalizeHex } from "@/components/ui/filament-chip";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

import { capsOf, isBusy } from "./caps";

const EXTERNAL_UNIT = 255;

export function unitLabel(id: number): string {
  return id >= 128 ? "AMS HT" : `AMS ${id + 1}`;
}

/** Trays holding the same material + colour in more than one slot: the printer can fall back between them. */
export function hasBackup(trays: AmsTray[]): boolean {
  const seen = new Set<string>();
  for (const t of trays) {
    const type = t.tray_type ?? t.material;
    const color = normalizeHex(t.color);
    if (!type || !color) continue;
    const key = `${type}|${color}`;
    if (seen.has(key)) return true;
    seen.add(key);
  }
  return false;
}

function SlotCircle({
  printer,
  tray,
  canControl,
  loaded,
  mapping,
  busy,
  label,
}: {
  printer: PrinterOut;
  tray: AmsTray;
  canControl: boolean;
  loaded: boolean;
  mapping?: string;
  busy: boolean;
  label: string;
}) {
  const control = useControl(printer.id);
  const color = normalizeHex(tray.color);
  const remain = tray.remain_pct != null && tray.remain_pct >= 0 ? Math.min(100, tray.remain_pct) : null;
  const material = tray.tray_type ?? tray.material;
  const circle = (
    <button
      type="button"
      data-testid={`slot-${tray.slot}`}
      aria-label={`${label} ${material ?? "empty"}`}
      className="relative flex size-8 items-center justify-center rounded-full border-2 border-border"
      style={color ? { backgroundColor: color } : undefined}
    >
      {!color ? <span className="pc-t9 text-muted-foreground">—</span> : null}
      {loaded ? (
        <span
          data-testid={`loaded-${tray.slot}`}
          className="absolute -right-0.5 -top-0.5 size-2.5 rounded-full bg-state-finished ring-2 ring-background"
        />
      ) : null}
    </button>
  );
  return (
    <div className="flex min-w-14 flex-1 flex-col items-center gap-0.5" data-slot="ams-slot">
      <div className="flex items-center">
        {canControl ? <SlotAssignPopover printerId={printer.id} tray={tray}>{circle}</SlotAssignPopover> : circle}
        {canControl ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="ghost" size="icon-xs" aria-label={`${label} menu`}>
                <MoreVerticalIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                disabled={busy || tray.ams_unit === EXTERNAL_UNIT}
                onSelect={() => void control.send("filament", { action: "load", tray: tray.slot })}
              >
                Load
              </DropdownMenuItem>
              <DropdownMenuItem disabled={busy} onSelect={() => void control.send("filament", { action: "unload" })}>
                Unload
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
      <span className="pc-t10 max-w-full truncate">{material ?? "Empty"}</span>
      {remain != null ? (
        <div
          role="meter"
          aria-label="Remaining"
          aria-valuenow={remain}
          className="h-1 w-full overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full bg-foreground/60" style={{ width: `${remain}%` }} />
        </div>
      ) : null}
      {tray.spool?.low_stock ? <Badge variant="destructive">Low</Badge> : null}
      {mapping ? <Badge variant="secondary">{mapping}</Badge> : null}
    </div>
  );
}

function UnitHeader({ id, unit, onDry }: { id: number; unit?: AmsUnit; onDry?: (unitId: number) => void }) {
  const detail = (
    <span className="pc-t10 flex items-center gap-2 font-mono tabular-nums text-muted-foreground">
      {unit?.humidity != null ? (
        <span className="flex items-center gap-0.5">
          <DropletsIcon className="size-3" />
          {Math.round(unit.humidity)}%
        </span>
      ) : null}
      {unit?.temp != null ? (
        <span className="flex items-center gap-0.5">
          <ThermometerIcon className="size-3" />
          {Math.round(unit.temp)}°
        </span>
      ) : null}
    </span>
  );
  return (
    <div className="flex items-center gap-2">
      <span className="pc-t10 font-medium">{unitLabel(id)}</span>
      {unit ? (
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>{detail}</TooltipTrigger>
            <TooltipContent>
              <div className="space-y-0.5 text-xs">
                <div>Humidity: {unit.humidity != null ? `${Math.round(unit.humidity)}%` : "--"}</div>
                <div>Temp: {unit.temp != null ? `${Math.round(unit.temp)} °C` : "--"}</div>
                {unit.serial ? <div>Serial: {unit.serial}</div> : null}
                {unit.fw ? <div>Firmware: {unit.fw}</div> : null}
              </div>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
      {onDry ? (
        <Button type="button" variant="outline" size="xs" className="ml-auto" onClick={() => onDry(id)}>
          Dry
        </Button>
      ) : null}
    </div>
  );
}

export function FilamentsSection({
  printer,
  status,
  canControl,
  mapping,
  onDry,
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
  /** tray slot -> mapping pill text (e.g. "F1"), from the active job once the API exposes it. */
  mapping?: Record<number, string>;
  /** AMS drying hook; the button renders only with the `ams_drying` capability AND a handler. */
  onDry?: (unitId: number) => void;
}) {
  const caps = capsOf(printer);
  const busy = isBusy(status.gcode_state);
  const ext = status.ext_tray ?? status.trays.find((t) => t.ams_unit === EXTERNAL_UNIT) ?? null;
  const ams = status.trays.filter((t) => t.ams_unit !== EXTERNAL_UNIT);
  if (ams.length === 0 && !ext) return null;

  const byUnit = new Map<number, AmsTray[]>();
  for (const t of ams) byUnit.set(t.ams_unit ?? 0, [...(byUnit.get(t.ams_unit ?? 0) ?? []), t]);
  const loadedSlot = status.tray_now ?? null;

  return (
    <div className="flex flex-col gap-2" data-slot="filaments-section">
      {hasBackup(ams) ? (
        <Badge variant="secondary" className="self-start" title="Two slots hold the same filament">
          AMS backup
        </Badge>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {[...byUnit.entries()].map(([unitId, trays]) => (
          <div key={unitId} className="flex min-w-0 flex-1 flex-col gap-1 rounded-md border p-2" data-slot="ams-unit">
            <UnitHeader
              id={unitId}
              unit={status.ams_units?.find((u) => u.id === unitId)}
              onDry={caps.ams_drying && canControl ? onDry : undefined}
            />
            <div className="flex gap-1">
              {trays.map((t) => (
                <SlotCircle
                  key={t.slot}
                  printer={printer}
                  tray={t}
                  canControl={canControl}
                  loaded={loadedSlot === t.slot}
                  mapping={mapping?.[t.slot]}
                  busy={busy}
                  label={`Slot ${t.slot + 1}`}
                />
              ))}
            </div>
          </div>
        ))}
        {ext ? (
          <div className="flex min-w-24 flex-col gap-1 rounded-md border p-2" data-slot="external-spool">
            <span className="pc-t10 font-medium">External</span>
            <SlotCircle
              printer={printer}
              tray={ext}
              canControl={canControl}
              loaded={loadedSlot === 254}
              busy={busy}
              label="External spool"
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
