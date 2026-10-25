/**
 * AMS unit cards (humidity / temperature, hover for serial + firmware),
 * slot circles (colour, fill bar, loaded marker, mapping pill, low-stock
 * flag) with a per-slot load/unload menu, the existing assign-spool popover
 * around each circle, the external spool card and an AMS-backup badge.
 */
import { DropletsIcon, MoreVerticalIcon, ThermometerIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useControl } from "@/api/printerControl";
import type { AmsTray, AmsUnit, PrinterOut, PrinterStatusOut } from "@/api/types";
import { SlotAssignPopover } from "@/components/inventory/SlotAssignPopover";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { normalizeHex } from "@/components/ui/filament-chip";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

import { AmsRenameDialog } from "./AmsRenameDialog";
import { KProfilesModal } from "@/components/print/modals/KProfilesModal";
import { printerNozzle } from "@/components/print/modals/kprofileUtils";
import { SlotConfigDialog } from "./SlotConfigDialog";
import { hasBackup, unitLabel } from "./ams";
import { DryChip } from "./DryChip";
import { capsOf, isBusy } from "./caps";

const EXTERNAL_UNIT = 255;


function SlotCircle({
  printer,
  tray,
  status,
  kprofiles,
  canControl,
  loaded,
  mapping,
  busy,
  label,
}: {
  printer: PrinterOut;
  tray: AmsTray;
  status: PrinterStatusOut;
  kprofiles: boolean;
  canControl: boolean;
  loaded: boolean;
  mapping?: string;
  busy: boolean;
  label: string;
}) {
  const { t } = useTranslation();
  const control = useControl(printer.id);
  const [configOpen, setConfigOpen] = useState(false);
  const [kOpen, setKOpen] = useState(false);
  const color = normalizeHex(tray.color);
  const remain = tray.remain_pct != null && tray.remain_pct >= 0 ? Math.min(100, tray.remain_pct) : null;
  const material = tray.tray_type ?? tray.material;
  const circle = (
    <button
      type="button"
      data-testid={`slot-${tray.slot}`}
      aria-label={`${label} ${material ?? t("card.filaments.emptyLower")}`}
      className="relative flex size-8 items-center justify-center rounded-full border-2 border-border"
      style={color ? { backgroundColor: color } : undefined}
    >
      {!color ? <span className="pc-t9 text-muted-foreground">—</span> : null}
      {loaded ? (
        <span
          data-testid={`loaded-${tray.slot}`}
          title={t("card.filaments.loaded")}
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
              <Button type="button" variant="ghost" size="icon-xs" aria-label={t("card.filaments.slotMenu", { label })}>
                <MoreVerticalIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                disabled={busy || tray.ams_unit === EXTERNAL_UNIT}
                onSelect={() => void control.send("filament", { action: "load", tray: tray.slot })}
              >
                {t("card.filaments.load")}
              </DropdownMenuItem>
              <DropdownMenuItem disabled={busy} onSelect={() => void control.send("filament", { action: "unload" })}>
                {t("card.filaments.unload")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setConfigOpen(true)}>{t("printerOps.slot.configure")}</DropdownMenuItem>
              {kprofiles ? <DropdownMenuItem onSelect={() => setKOpen(true)}>{t("kprofile.menu")}</DropdownMenuItem> : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
        {configOpen ? (
          <SlotConfigDialog
            printerId={printer.id}
            tray={tray}
            kprofiles={kprofiles}
            nozzle={printerNozzle(printer)}
            open
            onOpenChange={setConfigOpen}
          />
        ) : null}
        {kOpen ? (
          <KProfilesModal printer={printer} status={status} canControl={canControl} tray={tray} open onOpenChange={setKOpen} />
        ) : null}
      </div>
      <span className="pc-t10 max-w-full truncate">{material ?? t("card.filaments.empty")}</span>
      {remain != null ? (
        <div
          role="meter"
          aria-label={t("card.filaments.remaining")}
          aria-valuenow={remain}
          className="h-1 w-full overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full bg-foreground/60" style={{ width: `${remain}%` }} />
        </div>
      ) : null}
      {tray.spool?.low_stock ? <Badge variant="destructive">{t("card.filaments.low")}</Badge> : null}
      {mapping ? <Badge variant="secondary">{mapping}</Badge> : null}
    </div>
  );
}

function UnitHeader({
  printerId,
  id,
  unit,
  onDry,
  canRename,
  canStop,
  screenOnly,
}: {
  printerId: number;
  id: number;
  unit?: AmsUnit;
  onDry?: (unitId: number) => void;
  canRename?: boolean;
  canStop?: boolean;
  screenOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [renameOpen, setRenameOpen] = useState(false);
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
      <span className="pc-t10 font-medium">{unit?.label || unitLabel(id)}</span>
      {canRename && id < 254 ? (
        <>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="ghost" size="icon-xs" aria-label={t("card.filaments.unitMenu", { label: unit?.label || unitLabel(id) })}>
                <MoreVerticalIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuItem onSelect={() => setRenameOpen(true)}>{t("printerOps.ams.rename")}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {renameOpen ? (
            <AmsRenameDialog printerId={printerId} amsId={id} current={unit?.label ?? ""} open onOpenChange={setRenameOpen} />
          ) : null}
        </>
      ) : null}
      {unit ? (
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>{detail}</TooltipTrigger>
            <TooltipContent>
              <div className="space-y-0.5 text-xs">
                <div>{t("card.filaments.humidity", { v: unit.humidity != null ? `${Math.round(unit.humidity)}%` : "--" })}</div>
                <div>{t("card.filaments.temp", { v: unit.temp != null ? `${Math.round(unit.temp)} °C` : "--" })}</div>
                {unit.serial ? <div>{t("card.filaments.serial", { v: unit.serial })}</div> : null}
                {unit.fw ? <div>{t("card.filaments.firmware", { v: unit.fw })}</div> : null}
              </div>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
      {unit && (unit.dry_status ?? 0) > 0 ? (
        <DryChip printerId={printerId} id={id} unit={unit} canStop={!!canStop} screenOnly={!!screenOnly} />
      ) : null}
      {onDry && !screenOnly ? (
        <Button
          type="button"
          variant="outline"
          size="xs"
          className={(unit?.dry_status ?? 0) > 0 ? undefined : "ml-auto"}
          onClick={() => onDry(id)}
        >
          {t("card.filaments.dry")}
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
  const { t } = useTranslation();
  const caps = { ...capsOf(printer), ...(status.capabilities ?? {}) };
  const busy = isBusy(status.gcode_state);
  const ext = status.ext_tray ?? status.trays.find((x) => x.ams_unit === EXTERNAL_UNIT) ?? null;
  const ams = status.trays.filter((x) => x.ams_unit !== EXTERNAL_UNIT);
  if (ams.length === 0 && !ext) return null;

  const byUnit = new Map<number, AmsTray[]>();
  for (const x of ams) byUnit.set(x.ams_unit ?? 0, [...(byUnit.get(x.ams_unit ?? 0) ?? []), x]);
  // 254 = external spool, 255 = nothing loaded.
  const loadedSlot = status.tray_now ?? null;

  return (
    <div className="flex flex-col gap-2" data-slot="filaments-section">
      {hasBackup(ams) ? (
        <Badge variant="secondary" className="self-start" title={t("card.filaments.backupTip")}>
          {t("card.filaments.backup")}
        </Badge>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {[...byUnit.entries()].map(([unitId, trays]) => (
          <div key={unitId} className="flex min-w-0 flex-1 flex-col gap-1 rounded-md border p-2" data-slot="ams-unit">
            <UnitHeader
              printerId={printer.id}
              canRename={canControl}
              id={unitId}
              unit={status.ams_units?.find((u) => u.id === unitId)}
              onDry={caps.ams_drying && !caps.drying_screen_only && canControl ? onDry : undefined}
              canStop={caps.ams_drying && canControl}
              screenOnly={!!caps.drying_screen_only}
            />
            <div className="flex gap-1">
              {trays.map((tr) => (
                <SlotCircle
                  key={tr.slot}
                  printer={printer}
                  tray={tr}
                  status={status}
                  kprofiles={!!caps.kprofiles}
                  canControl={canControl}
                  loaded={loadedSlot === tr.slot}
                  mapping={mapping?.[tr.slot]}
                  busy={busy}
                  label={t("card.filaments.slot", { n: tr.slot + 1 })}
                />
              ))}
            </div>
          </div>
        ))}
        {ext ? (
          <div className="flex min-w-24 flex-col gap-1 rounded-md border p-2" data-slot="external-spool">
            <span className="pc-t10 font-medium">{t("card.filaments.external")}</span>
            <SlotCircle
              printer={printer}
              tray={ext}
              status={status}
              kprofiles={!!caps.kprofiles}
              canControl={canControl}
              loaded={loadedSlot === 254}
              busy={busy}
              label={t("card.filaments.externalSpool")}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
