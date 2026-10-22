/**
 * Live status card for one printer (M4 Task 8; restyled P3 Task 10): polls
 * `GET /printers/{id}/status` (`usePrinterStatus`) and renders the coarse
 * gcode_state as a `StateBadge` (the overview's shared state-tone
 * vocabulary, replacing the earlier raw `Badge`), a progress bar, a
 * `SpecRow` of layer/temp/remaining-time telemetry, and pause/resume/stop
 * controls gated on that state and on `canPrinter(id, "control")` (same
 * gate the overview's `ActiveJobStrip` uses).
 */
import { useAuth } from "@/api/auth";
import { usePrinterCommand, usePrinterStatus } from "@/api/printers";
import type { PrinterOut } from "@/api/types";
import { SlotAssignPopover } from "@/components/inventory/SlotAssignPopover";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FilamentChip } from "@/components/ui/filament-chip";
import { SpecRow } from "@/components/ui/spec-row";
import { StateBadge } from "@/components/ui/state-badge";
import { toneForGcode } from "@/lib/printerState";

export function PrinterStatusPanel({ printer }: { printer: PrinterOut }) {
  const status = usePrinterStatus(printer.id);
  const command = usePrinterCommand(printer.id);
  const { canPrinter } = useAuth();
  const s = status.data;
  const gs = s?.gcode_state ?? null;
  const printing = gs === "RUNNING";
  const paused = gs === "PAUSE";
  const { tone, label } = toneForGcode(!!s?.online, gs);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{printer.name}</CardTitle>
        <CardDescription>
          {printer.host} · {printer.serial}
        </CardDescription>
        <CardAction>
          <StateBadge tone={tone} label={label} pulse={tone === "printing"} />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {!s?.online ? (
          <p className="text-sm text-muted-foreground">Printer offline or printerd not running.</p>
        ) : (
          <>
            <div className="h-2 w-full overflow-hidden rounded bg-muted">
              <div className="h-2 rounded bg-primary transition-all" style={{ width: `${s.mc_percent ?? 0}%` }} />
            </div>
            <SpecRow
              items={[
                { label: `${s.mc_percent ?? 0}%` },
                { label: `layer ${s.layer_num ?? 0}/${s.total_layer_num ?? 0}` },
                { label: s.mc_remaining_time != null ? `${s.mc_remaining_time} min left` : "--" },
                { label: `nozzle ${s.nozzle_temper ?? "--"}°` },
                { label: `bed ${s.bed_temper ?? "--"}°` },
              ]}
            />
            {s.subtask_name ? <p className="text-sm">{s.subtask_name}</p> : null}
            {s.trays.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {s.trays.map((tray) => {
                  const chip = (
                    <FilamentChip color={tray.color ?? undefined} material={tray.material ?? undefined} remainPct={tray.remain_pct} />
                  );
                  return canPrinter(printer.id, "control") ? (
                    <SlotAssignPopover key={tray.slot} printerId={printer.id} tray={tray}>
                      <button type="button">{chip}</button>
                    </SlotAssignPopover>
                  ) : (
                    <span key={tray.slot}>{chip}</span>
                  );
                })}
              </div>
            ) : null}
            {s.print_error ? (
              <p role="alert" className="text-sm text-destructive">
                Printer error {s.print_error}
              </p>
            ) : null}
            {canPrinter(printer.id, "control") ? (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={!printing || command.isPending}
                onClick={() => command.mutate("pause")}
              >
                Pause
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!paused || command.isPending}
                onClick={() => command.mutate("resume")}
              >
                Resume
              </Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={!(printing || paused) || command.isPending}
                onClick={() => command.mutate("stop")}
              >
                Stop
              </Button>
            </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
