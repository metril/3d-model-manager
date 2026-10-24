/** T1: start/stop/schedule AMS drying for one unit. */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { startAfterIso, useDrying, useDryingPresets } from "@/api/drying";
import type { DryingPreset, PrinterOut, PrinterStatusOut } from "@/api/types";
import { ScheduledDryingsList } from "@/components/drying/ScheduledDryingsList";
import { unitLabel } from "@/components/print/card/ams";
import { capsOf, isBusy } from "@/components/print/card/caps";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

type Mode = "now" | "at" | "after";
const POWER = new Set([1, 8]);
const RETRACT = 3;

const TEMP_RANGE = [45, 85] as const;
const HOURS_RANGE = [1, 24] as const;
const clamp = (v: number, [lo, hi]: readonly [number, number] | readonly number[]) => Math.min(hi, Math.max(lo, v));

/** `n3s` = AMS-HT (ids >= 128), otherwise `n3f` (AMS 2 Pro / AMS). */
function presetValues(p: DryingPreset | undefined, unitId: number): { temp: number; hours: number } | null {
  if (!p) return null;
  return unitId >= 128 ? { temp: p.n3s_temp, hours: p.n3s_hours } : { temp: p.n3f_temp, hours: p.n3f_hours };
}

export function DryingDialog({
  printer,
  status,
  unitId,
  canControl,
  open,
  onOpenChange,
}: {
  printer: PrinterOut;
  status?: PrinterStatusOut;
  unitId: number;
  canControl: boolean;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const caps = { ...capsOf(printer), ...(status?.capabilities ?? {}) };
  const drying = useDrying(printer.id);
  const presets = useDryingPresets().data?.presets ?? {};
  const unit = status?.ams_units?.find((u) => u.id === unitId);
  const loaded = useMemo(
    () => [...new Set((status?.trays ?? []).filter((x) => (x.ams_unit ?? 0) === unitId).map((x) => x.tray_type).filter((x): x is string => !!x))],
    [status?.trays, unitId],
  );
  const filaments = [...new Set([...loaded, ...Object.keys(presets)])];
  const printing = isBusy(status?.gcode_state);
  const nowBlockedByPrint = printing && !caps.ams_drying_while_printing;

  const [filament, setFilament] = useState<string>(loaded[0] ?? "PLA");
  const [temp, setTemp] = useState("");
  const [hours, setHours] = useState("");
  const [touched, setTouched] = useState(false);
  const [rotate, setRotate] = useState(false);
  const [mode, setMode] = useState<Mode>(nowBlockedByPrint ? "after" : "now");
  const [atTime, setAtTime] = useState("");

  // Backfill temp/hours from the preset of the chosen filament until the user edits them.
  const preset = presetValues(presets[filament], unitId);
  const tempVal = temp !== "" || touched ? temp : String(preset?.temp ?? 45);
  const hoursVal = hours !== "" || touched ? hours : String(preset?.hours ?? 12);

  const codes = unit?.dry_sf_reason ?? [];
  const active = (unit?.dry_status ?? 0) > 0;
  const blocked = codes.length > 0 || active;
  const disabled = !canControl || drying.pending;

  function applyPreset(name: string) {
    setFilament(name);
    const v = presetValues(presets[name], unitId);
    if (v) {
      setTemp(String(v.temp));
      setHours(String(v.hours));
      setTouched(true);
    }
  }
  const finalTemp = clamp(Number(tempVal) || TEMP_RANGE[0], TEMP_RANGE);
  const finalHours = clamp(Math.round(Number(hoursVal)) || HOURS_RANGE[0], HOURS_RANGE);
  const startAfter = mode === "at" ? startAfterIso(atTime) : null;
  const startDisabled =
    disabled || (mode === "now" && (blocked || nowBlockedByPrint)) || (mode === "at" && !startAfter);

  async function submit() {
    const body = { ams_id: unitId, temp: finalTemp, duration_hours: finalHours, filament, rotate_tray: rotate };
    const ok =
      mode === "now"
        ? await drying.start(body)
        : await drying.schedule(startAfter ? { ...body, start_after: startAfter } : body);
    if (ok) onOpenChange(false);
  }

  const number = (value: string, set: (v: string) => void, range: readonly [number, number], label: string) => (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Input
        type="number"
        inputMode="numeric"
        min={range[0]}
        max={range[1]}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          setTouched(true);
          set(e.target.value);
        }}
        onBlur={() => set(String(clamp(Math.round(Number(value)) || range[0], range)))}
      />
    </div>
  );

  const modes: Array<{ id: Mode; show: boolean; disabled?: boolean }> = [
    { id: "now", show: true, disabled: nowBlockedByPrint },
    { id: "at", show: true },
    { id: "after", show: nowBlockedByPrint },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("drying.title", { unit: unit?.label || unitLabel(unitId) })}</DialogTitle>
          <DialogDescription>{t("drying.description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {codes.length > 0 ? (
            <div role="alert" className="space-y-1 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm" data-testid="dry-blocked">
              <div className="font-medium">{t("drying.blocked")}</div>
              <ul className="list-disc pl-4">
                {codes.map((c) => (
                  <li key={c}>
                    {t(`drying.reason.${c}`, { defaultValue: String(c) })}
                    <span className="text-muted-foreground">
                      {" "}
                      ({POWER.has(c) ? t("drying.blockedPower") : c === RETRACT ? t("drying.blockedRetract") : t("drying.blockedTransient")})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {active ? <p className="text-sm text-muted-foreground">{t("drying.active")}</p> : null}
          <div className="space-y-1">
            <Label>{t("drying.filament")}</Label>
            <Select value={filament} disabled={disabled} onValueChange={applyPreset}>
              <SelectTrigger aria-label={t("drying.filament")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[...new Set([filament, ...filaments])].map((f) => (
                  <SelectItem key={f} value={f}>
                    {f}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-wrap gap-1" aria-label={t("drying.presets")}>
            {Object.keys(presets).map((name) => (
              <Button key={name} type="button" size="xs" variant={name === filament ? "default" : "outline"} disabled={disabled} onClick={() => applyPreset(name)}>
                {name}
              </Button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3">
            {number(tempVal, setTemp, TEMP_RANGE, t("drying.temp"))}
            {number(hoursVal, setHours, HOURS_RANGE, t("drying.hours"))}
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={rotate} disabled={disabled} onCheckedChange={setRotate} aria-label={t("drying.rotate")} />
            <span className="text-sm">{t("drying.rotate")}</span>
          </div>
          <div className="space-y-2">
            <Label>{t("drying.startMode")}</Label>
            <div role="radiogroup" aria-label={t("drying.startMode")} className="inline-flex gap-1 rounded-md border p-0.5">
              {modes
                .filter((m) => m.show)
                .map((m) => (
                  <Button
                    key={m.id}
                    type="button"
                    size="xs"
                    role="radio"
                    aria-checked={mode === m.id}
                    variant={mode === m.id ? "default" : "ghost"}
                    disabled={m.disabled || disabled}
                    onClick={() => setMode(m.id)}
                  >
                    {t(`drying.mode.${m.id}`)}
                  </Button>
                ))}
            </div>
            {mode === "at" ? (
              <Input type="datetime-local" aria-label={t("drying.atTime")} value={atTime} onChange={(e) => setAtTime(e.target.value)} />
            ) : null}
            {mode === "after" ? <p className="text-xs text-muted-foreground">{t("drying.afterNote")}</p> : null}
          </div>
          <div className="space-y-1 border-t pt-3">
            <Label>{t("drying.scheduled")}</Label>
            <ScheduledDryingsList printerId={printer.id} amsId={unitId} compact />
          </div>
        </div>
        <DialogFooter>
          {active && canControl ? (
            <Button type="button" variant="destructive" disabled={drying.pending} onClick={() => void drying.stop(unitId).then((ok) => ok && onOpenChange(false))}>
              {t("drying.stop")}
            </Button>
          ) : null}
          <Button type="button" disabled={startDisabled} onClick={() => void submit()}>
            {mode === "now" ? t("drying.start") : t("drying.schedule")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
