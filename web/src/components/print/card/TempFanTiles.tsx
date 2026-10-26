/**
 * Temperature + fan tiles. Each tile shows `current -> target` (fans: %),
 * and clicking opens a popover with a number input and presets (PLA 220/60,
 * PETG 250/80, Off). Targets are applied optimistically and revert after
 * 10 s if the printer's reported target never matches. Chamber / aux /
 * chamber-fan / second nozzle tiles only exist when `capabilities` says so;
 * `verified === false` adds the "Untested on this model" tooltip.
 */
import { FanIcon, FlameIcon, ThermometerIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useControl } from "@/api/printerControl";
import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import { capsOf } from "./caps";
import { useOptimistic } from "./useOptimistic";
import { UntestedTip } from "./UntestedTip";

interface Preset {
  label: string;
  value: number;
}

function Tile({
  testId,
  label,
  icon,
  current,
  target,
  suffix,
  unit,
  max,
  presets,
  disabled,
  untested,
  spinning,
  onSet,
  hint,
}: {
  testId: string;
  label: string;
  icon: ReactNode;
  current: string;
  target: number | null;
  suffix?: boolean;
  unit: string;
  max: number;
  presets: Preset[];
  disabled: boolean;
  untested: boolean;
  spinning?: boolean;
  onSet: (value: number) => Promise<boolean>;
  hint?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const apply = async (v: number) => {
    const ok = await onSet(v);
    if (ok) setOpen(false);
  };
  const body = (
    <div
      data-testid={testId}
      className="flex min-w-0 flex-1 flex-col gap-0.5 rounded-md border px-2 py-1.5 text-left"
    >
      <span className="pc-t10 flex items-center gap-1 text-muted-foreground">
        <span className={spinning ? "pc-spin inline-flex" : "inline-flex"}>{icon}</span>
        {label}
      </span>
      <span className="pc-t12 font-mono tabular-nums">
        {current}
        {target != null && !suffix ? <span className="text-muted-foreground"> → {target}</span> : null}
        <span className="text-muted-foreground"> {unit}</span>
      </span>
    </div>
  );
  if (disabled && hint) {
    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <div tabIndex={0} className="flex min-w-0 flex-1">
              {body}
            </div>
          </TooltipTrigger>
          <TooltipContent>{untested ? `${hint} ${t("card.untested")}.` : hint}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
  if (disabled) return <UntestedTip show={untested}>{body}</UntestedTip>;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <UntestedTip show={untested}>
        <PopoverTrigger asChild>
          <button type="button" className="flex min-w-0 flex-1 text-left" aria-label={t("card.temps.set", { label })}>
            {body}
          </button>
        </PopoverTrigger>
      </UntestedTip>
      <PopoverContent className="w-56">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          {t("card.temps.target", { label, unit })}
          <Input
            type="number"
            min={0}
            max={max}
            value={draft}
            placeholder={target != null ? String(target) : "0"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && draft !== "") void apply(Math.min(max, Math.max(0, Number(draft))));
            }}
          />
        </label>
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
        <div className="flex flex-wrap gap-1.5">
          {presets.map((p) => (
            <Button key={p.label} type="button" size="xs" variant="outline" onClick={() => void apply(p.value)}>
              {p.label}
            </Button>
          ))}
          <Button
            type="button"
            size="xs"
            disabled={draft === ""}
            onClick={() => void apply(Math.min(max, Math.max(0, Number(draft))))}
          >
            {t("card.temps.apply")}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

const fmt = (v: number | null | undefined) => (v == null ? "--" : String(Math.round(v)));

function TempTile({
  printerId,
  tid,
  target,
  label,
  icon,
  current,
  reportedTarget,
  max,
  presets,
  disabled,
  untested,
  hint,
  extruder,
}: {
  printerId: number;
  /** Dual nozzle: 0 = right, 1 = left extruder (nozzle tiles only). */
  extruder?: 0 | 1;
  /** Locale-independent test id suffix. */
  tid: string;
  target: "nozzle" | "bed" | "chamber";
  label: string;
  icon: ReactNode;
  current: number | null | undefined;
  reportedTarget: number | null | undefined;
  max: number;
  presets: Preset[];
  disabled: boolean;
  untested: boolean;
  hint?: string;
}) {
  const { t } = useTranslation();
  const control = useControl(printerId);
  const [shown, setPending] = useOptimistic(reportedTarget);
  return (
    <Tile
      testId={`tile-${tid}`}
      label={label}
      icon={icon}
      current={fmt(current)}
      target={shown != null && shown > 0 ? Math.round(shown) : null}
      unit="°C"
      max={max}
      presets={[...presets, { label: t("card.temps.off"), value: 0 }]}
      disabled={disabled}
      untested={untested}
      hint={hint}
      onSet={async (value) => {
        setPending(value);
        const ok = await control.send("temperature", extruder == null ? { target, value } : { target, value, extruder_id: extruder });
        if (!ok) setPending(null);
        return ok;
      }}
    />
  );
}

function FanTile({
  printerId,
  fan,
  label,
  reported,
  disabled,
  untested,
}: {
  printerId: number;
  fan: "part" | "aux" | "chamber";
  label: string;
  reported: number | null | undefined;
  disabled: boolean;
  untested: boolean;
}) {
  const control = useControl(printerId);
  const [shown, setPending] = useOptimistic(reported);
  return (
    <Tile
      testId={`tile-fan-${fan}`}
      label={label}
      icon={<FanIcon className="pc-i" />}
      current={fmt(shown)}
      target={null}
      unit="%"
      max={100}
      spinning={(shown ?? 0) > 0}
      presets={[
        { label: "0%", value: 0 },
        { label: "50%", value: 50 },
        { label: "100%", value: 100 },
      ]}
      disabled={disabled}
      untested={untested}
      onSet={async (pct) => {
        setPending(pct);
        const ok = await control.send("fan", { fan, pct });
        if (!ok) setPending(null);
        return ok;
      }}
    />
  );
}

export function TempFanTiles({
  printer,
  status,
  canControl,
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
}) {
  const { t } = useTranslation();
  const caps = capsOf(printer);
  const disabled = !canControl || !status.online;
  const untested = !caps.verified;
  const maxNozzle = caps.max_nozzle ?? 300;
  const maxBed = caps.max_bed ?? 100;
  const nozzlePresets = [
    { label: "PLA 220", value: 220 },
    { label: "PETG 250", value: 250 },
  ];
  const bedPresets = [
    { label: "PLA 60", value: 60 },
    { label: "PETG 80", value: 80 },
  ];
  const common = { printerId: printer.id, disabled, untested };
  return (
    <div className="flex flex-col gap-1.5" data-slot="temp-fan-tiles">
      <div className="flex gap-1.5">
        {caps.dual_nozzle ? (
          <>
            {([1, 0] as const).map((id) => {
              const ext = status.extruders?.find((e) => e.id === id);
              const base = id === 1 ? t("card.temps.nozzleL") : t("card.temps.nozzleR");
              return (
                <TempTile
                  key={id}
                  {...common}
                  tid={id === 1 ? "nozzle-l" : "nozzle-r"}
                  target="nozzle"
                  extruder={id}
                  label={ext?.diameter ? `${base} ${ext.diameter}` : base}
                  icon={<ThermometerIcon className="pc-i" />}
                  current={ext ? ext.temp : id === 1 ? status.nozzle_temper : null}
                  reportedTarget={ext ? ext.target : id === 1 ? status.nozzle_target : null}
                  max={maxNozzle}
                  presets={nozzlePresets}
                  hint={ext?.type ? t("card.temps.nozzleType", { type: ext.type.replace(/_/g, " ") }) : undefined}
                />
              );
            })}
          </>
        ) : (
          <TempTile
            {...common}
            tid="nozzle"
            target="nozzle"
            label={t("card.temps.nozzle")}
            icon={<ThermometerIcon className="pc-i" />}
            current={status.nozzle_temper}
            reportedTarget={status.nozzle_target}
            max={maxNozzle}
            presets={nozzlePresets}
          />
        )}
        <TempTile
          {...common}
          tid="bed"
          target="bed"
          label={t("card.temps.bed")}
          icon={<FlameIcon className="pc-i" />}
          current={status.bed_temper}
          reportedTarget={status.bed_target}
          max={maxBed}
          presets={bedPresets}
        />
        {caps.chamber_temp || caps.chamber_heater ? (
          <TempTile
            {...common}
            disabled={disabled || !caps.chamber_heater}
            tid="chamber"
            target="chamber"
            label={t("card.temps.chamber")}
            icon={<ThermometerIcon className="pc-i" />}
            current={status.chamber_temper}
            reportedTarget={status.chamber_target}
            max={65}
            presets={[]}
            hint={caps.chamber_heater ? undefined : t("card.temps.chamberReadonly")}
          />
        ) : null}
      </div>
      <div className="flex gap-1.5">
        <FanTile {...common} fan="part" label={t("card.temps.partFan")} reported={status.fan_part_pct} />
        {caps.aux_fan ? <FanTile {...common} fan="aux" label={t("card.temps.auxFan")} reported={status.fan_aux_pct} /> : null}
        {caps.chamber_fan ? (
          <FanTile {...common} fan="chamber" label={t("card.temps.chamberFan")} reported={status.fan_chamber_pct} />
        ) : null}
      </div>
    </div>
  );
}
