import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useDryingSettings, useUpdateDryingSettings } from "@/api/drying";
import type { DryingPreset, DryingSettings } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

const inRange = (v: number, lo: number, hi: number) => Number.isFinite(v) && v >= lo && v <= hi;

/** Returns i18n error entries `[key, params]`; empty = valid. */
export function validateDrying(s: DryingSettings): Array<[string, Record<string, string>]> {
  const errors: Array<[string, Record<string, string>]> = [];
  for (const [name, p] of Object.entries(s.presets)) {
    if (!name.trim()) errors.push(["name", {}]);
    if (![p.n3f_temp, p.n3s_temp].every((v) => inRange(v, 45, 85))) errors.push(["temp", { name }]);
    if (![p.n3f_hours, p.n3s_hours].every((v) => Number.isInteger(v) && inRange(v, 1, 24))) errors.push(["hours", { name }]);
  }
  if (!inRange(s.ams_humidity_good, 0, 100) || !inRange(s.ams_humidity_fair, 0, 100) || s.ams_humidity_good > s.ams_humidity_fair) {
    errors.push(["humidity", {}]);
  }
  for (const [name, v] of Object.entries(s.per_type_thresholds)) {
    if (!name.trim()) errors.push(["name", {}]);
    if (!inRange(v, 0, 100)) errors.push(["threshold", { name }]);
  }
  if (!Number.isInteger(s.ambient_sustained_min) || !inRange(s.ambient_sustained_min, 1, 1440)) errors.push(["sustained", {}]);
  return errors;
}

const PRESET_FIELDS: Array<[keyof DryingPreset, "n3f" | "n3s", "temp" | "hours"]> = [
  ["n3f_temp", "n3f", "temp"],
  ["n3f_hours", "n3f", "hours"],
  ["n3s_temp", "n3s", "temp"],
  ["n3s_hours", "n3s", "hours"],
];

function Form({ initial }: { initial: DryingSettings }) {
  const { t } = useTranslation();
  const save = useUpdateDryingSettings();
  const [s, setS] = useState<DryingSettings>(initial);
  const [newFilament, setNewFilament] = useState("");
  const [newType, setNewType] = useState("");
  const errors = validateDrying(s);
  const num = (v: string) => (v === "" ? NaN : Number(v));
  const patch = (p: Partial<DryingSettings>) => setS((o) => ({ ...o, ...p }));
  const setPreset = (name: string, field: keyof DryingPreset, v: number) =>
    patch({ presets: { ...s.presets, [name]: { ...s.presets[name], [field]: v } } });
  const toggle = (label: string, key: keyof DryingSettings) => (
    <div className="flex items-center gap-3">
      <Switch checked={s[key] as boolean} onCheckedChange={(v) => patch({ [key]: v })} aria-label={label} />
      <span className="text-sm">{label}</span>
    </div>
  );

  return (
    <CardContent className="space-y-5">
      <div className="space-y-2">
        <Label>{t("drying.settings.presets")}</Label>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="pr-2 font-normal">{t("drying.settings.filament")}</th>
                <th className="px-1 font-normal" colSpan={2}>{t("drying.settings.n3f")}</th>
                <th className="px-1 font-normal" colSpan={2}>{t("drying.settings.n3s")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {Object.entries(s.presets).map(([name, p]) => (
                <tr key={name}>
                  <td className="pr-2 font-medium">{name}</td>
                  {PRESET_FIELDS.map(([field, unit, kind]) => (
                    <td key={field} className="px-1 py-0.5">
                      <Input
                        type="number"
                        className="h-8 w-16"
                        aria-label={`${name} ${t(`drying.settings.${unit}`)} ${kind === "temp" ? t("drying.settings.tempShort") : t("drying.settings.hoursShort")}`}
                        value={Number.isNaN(p[field]) ? "" : p[field]}
                        onChange={(e) => setPreset(name, field, num(e.target.value))}
                      />
                    </td>
                  ))}
                  <td>
                    <Button
                      type="button"
                      size="icon-xs"
                      variant="ghost"
                      aria-label={t("drying.settings.remove", { name })}
                      onClick={() => patch({ presets: Object.fromEntries(Object.entries(s.presets).filter(([k]) => k !== name)) })}
                    >
                      <Trash2 />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex gap-2">
          <Input className="h-8 w-40" placeholder={t("drying.settings.newFilament")} aria-label={t("drying.settings.newFilament")} value={newFilament} onChange={(e) => setNewFilament(e.target.value)} />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!newFilament.trim() || newFilament.trim() in s.presets}
            onClick={() => {
              patch({ presets: { ...s.presets, [newFilament.trim()]: { n3f_temp: 55, n3s_temp: 55, n3f_hours: 12, n3s_hours: 12 } } });
              setNewFilament("");
            }}
          >
            {t("drying.settings.addFilament")}
          </Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label>{t("drying.settings.good")}</Label>
          <Input type="number" value={Number.isNaN(s.ams_humidity_good) ? "" : s.ams_humidity_good} onChange={(e) => patch({ ams_humidity_good: num(e.target.value) })} />
        </div>
        <div className="space-y-1">
          <Label>{t("drying.settings.fair")}</Label>
          <Input type="number" value={Number.isNaN(s.ams_humidity_fair) ? "" : s.ams_humidity_fair} onChange={(e) => patch({ ams_humidity_fair: num(e.target.value) })} />
        </div>
      </div>

      <div className="space-y-2">
        <Label>{t("drying.settings.perType")}</Label>
        {Object.entries(s.per_type_thresholds).map(([name, v]) => (
          <div key={name} className="flex items-center gap-2">
            <span className="w-24 text-sm">{name}</span>
            <Input
              type="number"
              className="h-8 w-20"
              aria-label={`${name} ${t("drying.settings.threshold")}`}
              value={Number.isNaN(v) ? "" : v}
              onChange={(e) => patch({ per_type_thresholds: { ...s.per_type_thresholds, [name]: num(e.target.value) } })}
            />
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              aria-label={t("drying.settings.remove", { name })}
              onClick={() => patch({ per_type_thresholds: Object.fromEntries(Object.entries(s.per_type_thresholds).filter(([k]) => k !== name)) })}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
        <div className="flex gap-2">
          <Input className="h-8 w-40" placeholder={t("drying.settings.typeName")} aria-label={t("drying.settings.typeName")} value={newType} onChange={(e) => setNewType(e.target.value)} />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!newType.trim() || newType.trim() in s.per_type_thresholds}
            onClick={() => {
              patch({ per_type_thresholds: { ...s.per_type_thresholds, [newType.trim()]: s.ams_humidity_fair } });
              setNewType("");
            }}
          >
            {t("drying.settings.addType")}
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        {toggle(t("drying.settings.ambient"), "ambient_drying_enabled")}
        <div className="space-y-1 pl-12">
          <Label>{t("drying.settings.sustained")}</Label>
          <Input
            type="number"
            className="w-28"
            disabled={!s.ambient_drying_enabled}
            value={Number.isNaN(s.ambient_sustained_min) ? "" : s.ambient_sustained_min}
            onChange={(e) => patch({ ambient_sustained_min: num(e.target.value) })}
          />
        </div>
        {toggle(t("drying.settings.queue"), "queue_drying_enabled")}
        <div className="pl-12">{toggle(t("drying.settings.queueBlock"), "queue_drying_block")}</div>
        {toggle(t("drying.settings.print"), "print_drying_enabled")}
      </div>

      {errors.length > 0 ? (
        <ul role="alert" className="list-disc pl-4 text-sm text-destructive">
          {errors.map(([key, params], i) => (
            <li key={i}>{t(`drying.settings.invalid.${key}`, params)}</li>
          ))}
        </ul>
      ) : null}
      <Button type="button" disabled={errors.length > 0 || save.isPending} onClick={() => save.mutate(s)}>
        {t("drying.settings.save")}
      </Button>
    </CardContent>
  );
}

export function DryingSettingsCard() {
  const { t } = useTranslation();
  const settings = useDryingSettings();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("drying.settings.title")}</CardTitle>
        <CardDescription>{t("drying.settings.description")}</CardDescription>
      </CardHeader>
      {settings.data ? <Form key={JSON.stringify(settings.data)} initial={settings.data} /> : null}
    </Card>
  );
}
