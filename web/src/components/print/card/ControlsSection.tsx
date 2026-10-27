/**
 * Live controls, one compact row: chamber-light toggle (icon button), print
 * speed segmented control, and the airduct / active-nozzle groups when the model
 * has them. Movement, calibration and Pause/Stop moved to the toolbar and the
 * card footer. Everything is hidden when the viewer cannot control.
 */
import { LightbulbIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useControl } from "@/api/printerControl";
import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { capsOf, isBusy } from "./caps";
import { ToolButton } from "./ToolButton";
import { UntestedTip } from "./UntestedTip";
import { useOptimistic } from "./useOptimistic";

const SPEED_LEVELS = [
  { level: 1, key: "card.controls.silent" },
  { level: 2, key: "card.controls.standard" },
  { level: 3, key: "card.controls.sport" },
  { level: 4, key: "card.controls.ludicrous" },
] as const;

const SEGMENT = "inline-flex items-center gap-0.5 rounded-lg bg-muted p-0.5";
const SEG_BTN = "pc-t11 rounded-md px-2 py-1 font-medium transition-colors disabled:opacity-50";
const SEG_ON = "bg-background text-foreground shadow-sm";
const SEG_OFF = "text-muted-foreground hover:text-foreground";

export function ControlsSection({
  printer,
  status,
  canControl,
  labeled = false,
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
  /** Show the "Live controls" caption (XL cards / detail page). */
  labeled?: boolean;
}) {
  const { t } = useTranslation();
  const caps = capsOf(printer);
  const control = useControl(printer.id);
  const [speed, setSpeed] = useOptimistic(status.speed_level);
  const [light, setLight] = useState<boolean | null>(null);
  const lightTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(lightTimer.current), []);
  const untested = !caps.verified;
  if (!canControl || !status.online) return null;

  const busy = isBusy(status.gcode_state);
  const lightOn = light ?? status.light_on ?? false;

  return (
    <div className="flex flex-col gap-1.5" data-slot="controls-section">
      {labeled ? (
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("card.controls.section")}</span>
      ) : null}
      <TooltipProvider delayDuration={200}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <ToolButton
            label={t("card.controls.lightLabel")}
            icon={<LightbulbIcon />}
            untested={untested}
            variant={lightOn ? "default" : "outline"}
            aria-pressed={lightOn}
            onClick={async () => {
              clearTimeout(lightTimer.current);
              setLight(!lightOn);
              const ok = await control.send("light", { on: !lightOn });
              if (!ok) setLight(null);
              else {
                clearTimeout(lightTimer.current);
                lightTimer.current = setTimeout(() => setLight(null), 5000);
              }
            }}
          />
          <div role="radiogroup" aria-label={t("card.controls.speed")} className={SEGMENT}>
            {SPEED_LEVELS.map(({ level, key }) => (
              <UntestedTip key={level} show={untested}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={speed === level}
                  className={cn(SEG_BTN, speed === level ? SEG_ON : SEG_OFF)}
                  onClick={async () => {
                    setSpeed(level);
                    if (!(await control.send("speed", { level }))) setSpeed(null);
                  }}
                >
                  {t(key)}
                </button>
              </UntestedTip>
            ))}
          </div>
          {caps.airduct ? (
            <div role="group" aria-label={t("card.controls.airduct")} className={SEGMENT}>
              {(["cooling", "heating"] as const).map((mode) => (
                <UntestedTip key={mode} show={untested}>
                  <button
                    type="button"
                    className={cn(SEG_BTN, SEG_OFF, "capitalize")}
                    onClick={() => void control.send("airduct", { mode })}
                  >
                    {t(mode === "cooling" ? "card.controls.cooling" : "card.controls.heating")}
                  </button>
                </UntestedTip>
              ))}
            </div>
          ) : null}
          {caps.dual_nozzle ? (
            <div role="group" aria-label={t("card.controls.extruder")} className={SEGMENT}>
              {([1, 0] as const).map((id) => (
                <UntestedTip key={id} show={untested}>
                  <button
                    type="button"
                    aria-pressed={status.active_extruder === id}
                    disabled={busy}
                    data-testid={`extruder-${id === 1 ? "left" : "right"}`}
                    className={cn(SEG_BTN, status.active_extruder === id ? SEG_ON : SEG_OFF)}
                    onClick={() => void control.send("extruder", { extruder_id: id })}
                  >
                    {t(id === 1 ? "card.controls.extruderLeft" : "card.controls.extruderRight")}
                  </button>
                </UntestedTip>
              ))}
            </div>
          ) : null}
        </div>
      </TooltipProvider>
    </div>
  );
}
