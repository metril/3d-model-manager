/**
 * Live controls, ghost-icon style: chamber-light toggle (lamp tint when on),
 * print-speed chip (radio menu with % and hints; a two-line segmented control
 * at L/XL), and the airduct / active-nozzle groups when the model has them.
 * Movement and Camera sit beside this in the toolbar. Hidden when the viewer
 * cannot control or the printer is offline.
 */
import { GaugeIcon, LightbulbIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useControl } from "@/api/printerControl";
import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import type { CardSize } from "./cardSize";
import { capsOf, isBusy } from "./caps";
import { ToolButton } from "./ToolButton";
import { UntestedTip } from "./UntestedTip";
import { useOptimistic } from "./useOptimistic";

/** Bambu speed profiles: level -> label / % of nominal speed / hint. */
const SPEED_LEVELS = [
  { level: 1, key: "card.controls.silent", pct: 50, hint: "card.controls.silentHint" },
  { level: 2, key: "card.controls.standard", pct: 100, hint: "card.controls.standardHint" },
  { level: 3, key: "card.controls.sport", pct: 124, hint: "card.controls.sportHint" },
  { level: 4, key: "card.controls.ludicrous", pct: 166, hint: "card.controls.ludicrousHint" },
] as const;

const SEGMENT = "inline-flex items-center gap-0.5 rounded-lg bg-muted p-0.5";
const SEG_BTN = "pc-t11 rounded-md px-2 py-1 font-medium transition-colors disabled:opacity-50";
const SEG_ON = "bg-background text-foreground shadow-sm";
const SEG_OFF = "text-muted-foreground hover:text-foreground";

export function ControlsSection({
  printer,
  status,
  canControl,
  size = "M",
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
  size?: CardSize;
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
  const large = size === "L" || size === "XL";
  const current = SPEED_LEVELS.find((s) => s.level === speed);
  const nonStandard = current != null && current.level !== 2;

  const tip = (text: string) => (
    <>
      <span className="block">{text}</span>
      {untested ? <span className="block opacity-70">{t("card.untested")}</span> : null}
    </>
  );

  async function pickSpeed(level: number) {
    setSpeed(level);
    if (!(await control.send("speed", { level }))) setSpeed(null);
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1.5" data-slot="controls-section">
        <ToolButton
          label={t("card.controls.lightLabel")}
          icon={<LightbulbIcon />}
          untested={untested}
          variant="ghost"
          className={cn(lightOn && "bg-warning/15 text-warning hover:bg-warning/20 hover:text-warning")}
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
        {large ? (
          <div role="radiogroup" aria-label={t("card.controls.speed")} className={SEGMENT} data-testid="speed-segmented">
            {SPEED_LEVELS.map(({ level, key, pct, hint }) => (
              <Tooltip key={level}>
                <TooltipTrigger asChild>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={speed === level}
                      aria-label={t(key)}
                      className={cn(
                        SEG_BTN,
                        "flex flex-col items-center leading-tight",
                        speed === level ? SEG_ON : SEG_OFF,
                        speed === level && level !== 2 && "text-warning",
                      )}
                      onClick={() => void pickSpeed(level)}
                    >
                      <span>{t(key)}</span>
                      <span className="font-mono text-[0.85em] font-normal tabular-nums opacity-80">{pct}%</span>
                    </button>
                </TooltipTrigger>
                <TooltipContent>{tip(t(hint))}</TooltipContent>
              </Tooltip>
            ))}
          </div>
        ) : (
          <DropdownMenu>
            <Tooltip>
              <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      data-testid="speed-chip"
                      className={cn("font-mono tabular-nums", nonStandard && "text-warning hover:text-warning")}
                    >
                      <GaugeIcon />
                      {current ? t("card.controls.speedChip", { name: t(current.key), pct: current.pct }) : t("card.controls.speed")}
                    </Button>
                  </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent>{tip(t("card.controls.speed"))}</TooltipContent>
            </Tooltip>
            <DropdownMenuContent align="start" className="w-64">
              <DropdownMenuRadioGroup value={String(speed ?? "")} onValueChange={(v) => void pickSpeed(Number(v))}>
                {SPEED_LEVELS.map(({ level, key, pct, hint }) => (
                  <DropdownMenuRadioItem key={level} value={String(level)} className="items-start py-1.5">
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="flex items-baseline justify-between gap-3">
                        <span className="font-medium">{t(key)}</span>
                        <span className="font-mono text-xs tabular-nums text-muted-foreground">{pct}%</span>
                      </span>
                      <span className="text-xs text-muted-foreground">{t(hint)}</span>
                    </span>
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
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
  );
}
