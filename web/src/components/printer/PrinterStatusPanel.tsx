/**
 * Live status card for one printer (M4 Task 8): polls `GET
 * /printers/{id}/status` (`usePrinterStatus`) and renders the coarse
 * gcode_state as a badge, a progress bar, layer/temp/remaining-time
 * telemetry, and pause/resume/stop controls gated on that state.
 */
import { useAuth } from "@/api/auth";
import { usePrinterCommand, usePrinterStatus } from "@/api/printers";
import type { PrinterOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function PrinterStatusPanel({ printer }: { printer: PrinterOut }) {
  const status = usePrinterStatus(printer.id);
  const command = usePrinterCommand(printer.id);
  const { canPrinter } = useAuth();
  const s = status.data;
  const gs = s?.gcode_state ?? null;
  const printing = gs === "RUNNING";
  const paused = gs === "PAUSE";

  return (
    <Card>
      <CardHeader>
        <CardTitle>{printer.name}</CardTitle>
        <CardDescription>
          {printer.host} · {printer.serial}
        </CardDescription>
        <CardAction>
          <Badge variant={s?.online ? "default" : "outline"}>{s?.online ? (gs ?? "online") : "offline"}</Badge>
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
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
              <span>{s.mc_percent ?? 0}%</span>
              <span>
                layer {s.layer_num ?? 0}/{s.total_layer_num ?? 0}
              </span>
              <span>{s.mc_remaining_time != null ? `${s.mc_remaining_time} min left` : "--"}</span>
              <span>nozzle {s.nozzle_temper ?? "--"}°</span>
              <span>bed {s.bed_temper ?? "--"}°</span>
            </div>
            {s.subtask_name ? <p className="text-sm">{s.subtask_name}</p> : null}
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
