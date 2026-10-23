/**
 * Light toggle, speed segmented control, movement popover (XY/Z jog with a
 * 1/10/50 mm step, home, extrude/retract -- disabled while printing),
 * airduct (capability), calibration, and Pause/Resume/Stop behind a
 * ConfirmDialog. Everything is hidden when the viewer cannot control.
 */
import { LightbulbIcon, MoveIcon, WrenchIcon } from "lucide-react";
import { useState } from "react";

import { useControl } from "@/api/printerControl";
import { usePrinterCommand } from "@/api/printers";
import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { capsOf, isBusy } from "./caps";
import { useOptimistic } from "./TempFanTiles";
import { UntestedTip } from "./UntestedTip";

export const SPEED_LEVELS = [
  { level: 1, label: "Silent" },
  { level: 2, label: "Standard" },
  { level: 3, label: "Sport" },
  { level: 4, label: "Ludicrous" },
] as const;
const STEPS = [1, 10, 50] as const;

function MovementPopover({ printerId, busy, untested }: { printerId: number; busy: boolean; untested: boolean }) {
  const control = useControl(printerId);
  const [step, setStep] = useState<number>(10);
  const jog = (axis: "X" | "Y" | "Z", sign: 1 | -1) => void control.send("jog", { axis, mm: sign * step });
  const btn = (label: string, onClick: () => void, key?: string) => (
    <Button key={key ?? label} type="button" size="sm" variant="outline" disabled={busy || control.pending} onClick={onClick}>
      {label}
    </Button>
  );
  return (
    <Popover>
      <UntestedTip show={untested}>
        <PopoverTrigger asChild>
          <Button type="button" size="sm" variant="outline" disabled={busy} aria-label="Movement">
            <MoveIcon />
            Move
          </Button>
        </PopoverTrigger>
      </UntestedTip>
      <PopoverContent className="w-60">
        <div className="flex items-center gap-1" role="radiogroup" aria-label="Jog step">
          {STEPS.map((s) => (
            <Button
              key={s}
              type="button"
              size="xs"
              role="radio"
              aria-checked={step === s}
              variant={step === s ? "default" : "outline"}
              onClick={() => setStep(s)}
            >
              {s} mm
            </Button>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-1">
          <span />
          {btn("Y+", () => jog("Y", 1))}
          <span />
          {btn("X-", () => jog("X", -1))}
          {btn("Home", () => void control.send("home"))}
          {btn("X+", () => jog("X", 1))}
          <span />
          {btn("Y-", () => jog("Y", -1))}
          <span />
        </div>
        <div className="grid grid-cols-2 gap-1">
          {btn("Z+", () => jog("Z", 1))}
          {btn("Z-", () => jog("Z", -1))}
          {btn("Extrude", () => void control.send("extrude", { mm: 10 }))}
          {btn("Retract", () => void control.send("extrude", { mm: -10 }))}
        </div>
        <p className="text-xs text-muted-foreground">Extrude needs the nozzle at 170 °C or hotter.</p>
      </PopoverContent>
    </Popover>
  );
}

export function ControlsSection({
  printer,
  status,
  canControl,
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
}) {
  const caps = capsOf(printer);
  const control = useControl(printer.id);
  const command = usePrinterCommand(printer.id);
  const [speed, setSpeed] = useOptimistic(status.speed_level);
  const [light, setLight] = useState<boolean | null>(null);
  const untested = !caps.verified;
  if (!canControl || !status.online) return null;

  const busy = isBusy(status.gcode_state);
  const printing = status.gcode_state === "RUNNING" || status.gcode_state === "PREPARE";
  const paused = status.gcode_state === "PAUSE";
  const lightOn = light ?? status.light_on ?? false;

  return (
    <div className="flex flex-col gap-2" data-slot="controls-section">
      <div className="flex flex-wrap items-center gap-2">
        <UntestedTip show={untested}>
          <Button
            type="button"
            size="sm"
            variant={lightOn ? "default" : "outline"}
            aria-pressed={lightOn}
            aria-label="Chamber light"
            onClick={async () => {
              setLight(!lightOn);
              const ok = await control.send("light", { on: !lightOn });
              if (!ok) setLight(null);
              else setTimeout(() => setLight(null), 5000);
            }}
          >
            <LightbulbIcon />
            Light
          </Button>
        </UntestedTip>
        <div role="radiogroup" aria-label="Print speed" className="inline-flex overflow-hidden rounded-md border">
          {SPEED_LEVELS.map(({ level, label }) => (
            <UntestedTip key={level} show={untested}>
              <button
                type="button"
                role="radio"
                aria-checked={speed === level}
                className={cn(
                  "pc-t11 px-2 py-1 transition-colors",
                  speed === level ? "bg-primary text-primary-foreground" : "hover:bg-muted",
                )}
                onClick={async () => {
                  setSpeed(level);
                  if (!(await control.send("speed", { level }))) setSpeed(null);
                }}
              >
                {label}
              </button>
            </UntestedTip>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <MovementPopover printerId={printer.id} busy={busy} untested={untested} />
        {caps.airduct ? (
          <div role="group" aria-label="Airduct" className="inline-flex overflow-hidden rounded-md border">
            {(["cooling", "heating"] as const).map((mode) => (
              <UntestedTip key={mode} show={untested}>
                <button
                  type="button"
                  className="pc-t11 px-2 py-1 capitalize hover:bg-muted"
                  onClick={() => void control.send("airduct", { mode })}
                >
                  {mode}
                </button>
              </UntestedTip>
            ))}
          </div>
        ) : null}
        <ConfirmDialog
          trigger={
            <Button type="button" size="sm" variant="outline" disabled={busy} aria-label="Calibration">
              <WrenchIcon />
              Calibrate
            </Button>
          }
          title="Run calibration?"
          description="Bed levelling, vibration compensation and motor noise cancellation. The printer will move."
          confirmLabel="Calibrate"
          onConfirm={() => void control.send("calibrate", {})}
        />
        {printing || paused ? (
          <div className="ml-auto flex gap-2">
            <ConfirmDialog
              trigger={
                <Button type="button" size="sm" variant="outline" disabled={command.isPending}>
                  {paused ? "Resume" : "Pause"}
                </Button>
              }
              title={paused ? "Resume this print?" : "Pause this print?"}
              confirmLabel={paused ? "Resume" : "Pause"}
              onConfirm={() => command.mutate(paused ? "resume" : "pause")}
            />
            <ConfirmDialog
              trigger={
                <Button type="button" size="sm" variant="destructive" disabled={command.isPending}>
                  Stop
                </Button>
              }
              title="Stop this print?"
              description="The printer will cancel the job in progress. This can't be undone."
              confirmLabel="Stop"
              destructive
              onConfirm={() => command.mutate("stop")}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
