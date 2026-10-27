import { MoveIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useControl } from "@/api/printerControl";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import { ToolButton } from "./ToolButton";

const STEPS = [1, 10, 50] as const;

/** XY/Z jog (1/10/50 mm), home, extrude/retract. Trigger is a toolbar icon button. */
export function MovementPopover({
  printerId,
  busy,
  untested,
  showLabel,
}: {
  printerId: number;
  busy: boolean;
  untested: boolean;
  showLabel?: boolean;
}) {
  const { t } = useTranslation();
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
      <PopoverTrigger asChild>
        <ToolButton
          label={t("card.controls.movement")}
          icon={<MoveIcon />}
          showLabel={showLabel}
          untested={untested}
          disabled={busy}
        />
      </PopoverTrigger>
      <PopoverContent className="w-60">
        <div className="flex items-center gap-1" role="radiogroup" aria-label={t("card.controls.jogStep")}>
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
              {t("card.controls.mm", { n: s })}
            </Button>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-1">
          <span />
          {btn("Y+", () => jog("Y", 1))}
          <span />
          {btn("X-", () => jog("X", -1))}
          {btn(t("card.controls.home"), () => void control.send("home"))}
          {btn("X+", () => jog("X", 1))}
          <span />
          {btn("Y-", () => jog("Y", -1))}
          <span />
        </div>
        <div className="grid grid-cols-2 gap-1">
          {btn("Z+", () => jog("Z", 1))}
          {btn("Z-", () => jog("Z", -1))}
          {btn(t("card.controls.extrude"), () => void control.send("extrude", { mm: 10 }))}
          {btn(t("card.controls.retract"), () => void control.send("extrude", { mm: -10 }))}
        </div>
        <p className="text-xs text-muted-foreground">{t("card.controls.extrudeHint")}</p>
      </PopoverContent>
    </Popover>
  );
}
