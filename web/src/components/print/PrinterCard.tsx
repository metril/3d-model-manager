/**
 * One printer's dashboard card (P3 Task 7): a 16:9 camera tile (default
 * placeholder, `camera` slot for a real feed -- `PrintDashboardPage` (P4
 * Task 9) fills it with a live `CameraTile` for `can_control` printers)
 * with the state badge overlaid top-left and a `badge` slot top-right
 * (the "primary printer" pin, P3 renders nothing there either), then the
 * active job or an "Idle" line, nozzle/bed temps, and AMS tray chips.
 * Header links to the printer's detail page.
 */
import { Link } from "@tanstack/react-router";
import { PrinterIcon } from "lucide-react";

import type { PrinterOverviewItem } from "@/api/types";
import { SlotAssignPopover } from "@/components/inventory/SlotAssignPopover";
import { ActiveJobStrip } from "@/components/print/ActiveJobStrip";
import { FilamentChip } from "@/components/ui/filament-chip";
import { SpecRow } from "@/components/ui/spec-row";
import { StateBadge } from "@/components/ui/state-badge";
import { toneForGcode } from "@/lib/printerState";

export function PrinterCard({
  item,
  camera,
  badge,
}: {
  item: PrinterOverviewItem;
  camera?: React.ReactNode;
  badge?: React.ReactNode;
}) {
  const { printer, status, active_job: activeJob, can_control: canControl } = item;
  const { tone, label } = toneForGcode(status.online, status.gcode_state);

  return (
    <div data-slot="printer-card" className="overflow-hidden rounded-[6px] border">
      <div className="relative aspect-video bg-muted">
        {camera ?? (
          <div className="flex size-full items-center justify-center">
            <PrinterIcon className="size-8 text-muted-foreground/50" />
          </div>
        )}
        <div className="absolute left-2 top-2 rounded-[6px] bg-background/80 px-1.5 py-0.5">
          <StateBadge tone={tone} label={label} pulse={tone === "printing"} />
        </div>
        {badge != null ? <div className="absolute right-2 top-2">{badge}</div> : null}
      </div>

      <div className="flex flex-col gap-2 p-3">
        <Link to="/printers/$id" params={{ id: String(printer.id) }} className="text-sm font-medium hover:underline">
          {printer.name}
        </Link>

        {activeJob ? (
          <ActiveJobStrip job={activeJob} status={status} printerId={printer.id} canControl={canControl} />
        ) : (
          <p className="text-sm text-muted-foreground">Idle</p>
        )}

        <SpecRow
          items={[
            {
              label: (
                <span className="font-mono">
                  {status.nozzle_temper ?? "--"} / {status.bed_temper ?? "--"}
                  <span className="text-muted-foreground"> °C</span>
                </span>
              ),
            },
          ]}
        />

        {status.trays.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {status.trays.map((tray) => {
              const chip = (
                <FilamentChip
                  color={tray.color ?? undefined}
                  material={tray.material ?? undefined}
                  lowStock={tray.spool?.low_stock ?? false}
                  remainPct={tray.remain_pct}
                />
              );
              return canControl ? (
                <SlotAssignPopover key={tray.slot} printerId={printer.id} tray={tray}>
                  <button type="button">{chip}</button>
                </SlotAssignPopover>
              ) : (
                <span key={tray.slot}>{chip}</span>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
