/**
 * T4: Settings -> Printer "Queue preheat" card (`GET/PUT /settings/queue-preheat`):
 * heat the chamber (bed soak on printers without a chamber heater) before a
 * queued print starts, plus keep-warm between prints.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useQueuePreheatSettings, useUpdateQueuePreheatSettings } from "@/api/queue";
import type { QueuePreheatSettings } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

type Row = { name: string; temp: string };

interface Draft {
  enabled: boolean;
  chamber: Row[];
  bed: Row[];
  maxWait: string;
  soak: string;
  keepWarm: boolean;
  keepBed: string;
  keepMax: string;
}

const toRows = (m: Record<string, number> | undefined): Row[] => Object.entries(m ?? {}).map(([name, v]) => ({ name, temp: String(v) }));

function seed(s: QueuePreheatSettings): Draft {
  return {
    enabled: !!s.enabled,
    chamber: toRows(s.chamber_targets),
    bed: toRows(s.bed_targets),
    maxWait: String(s.max_wait_s ?? 600),
    soak: String(s.soak_s),
    keepWarm: s.keep_warm_enabled,
    keepBed: String(s.keep_warm_bed_c),
    keepMax: String(s.keep_warm_max_min),
  };
}

const inRange = (raw: string, lo: number, hi: number) => {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isInteger(n) && n >= lo && n <= hi;
};

function RowsEditor({
  label,
  rows,
  onChange,
  idPrefix,
}: {
  label: string;
  rows: Row[];
  onChange: (r: Row[]) => void;
  idPrefix: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            aria-label={`${label} ${t("queueSched.preheat_card.filament")} ${i + 1}`}
            className="w-32"
            value={r.name}
            onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
          />
          <Input
            aria-label={`${label} ${t("queueSched.preheat_card.temp")} ${i + 1}`}
            id={`${idPrefix}-${i}`}
            type="number"
            className="w-24"
            value={r.temp}
            onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, temp: e.target.value } : x)))}
          />
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            {t("queueSched.preheat_card.remove")}
          </Button>
        </div>
      ))}
      <Button type="button" size="sm" variant="outline" onClick={() => onChange([...rows, { name: "", temp: "" }])}>
        {t("queueSched.preheat_card.add")}
      </Button>
    </div>
  );
}

export function QueuePreheatCard() {
  const { t } = useTranslation();
  const settings = useQueuePreheatSettings();
  const update = useUpdateQueuePreheatSettings();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data && draft === null) setDraft(seed(settings.data));
  }, [settings.data, draft]);

  if (settings.isLoading || (draft === null && !settings.isError)) return <Skeleton className="h-56 w-full rounded-xl" />;
  if (settings.isError || draft === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("queueSched.preheat_card.title")}</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : t("queueSched.preheat_card.loadError")}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const set = (patch: Partial<Draft>) => {
    setSaved(false);
    setDraft((d) => (d ? { ...d, ...patch } : d));
  };

  function rowsToMap(rows: Row[], errs: string[]): Record<string, number> {
    const out: Record<string, number> = {};
    for (const r of rows) {
      if (!r.name.trim()) {
        errs.push(t("queueSched.preheat_card.errName"));
        continue;
      }
      if (!inRange(r.temp, 0, 120)) errs.push(t("queueSched.preheat_card.errTemp"));
      else out[r.name.trim()] = Number(r.temp);
    }
    return out;
  }

  function save() {
    if (!draft) return;
    const errs: string[] = [];
    const chamber = rowsToMap(draft.chamber, errs);
    const bed = rowsToMap(draft.bed, errs);
    if (!inRange(draft.maxWait, 60, 3600)) errs.push(t("queueSched.preheat_card.errMaxWait"));
    if (!inRange(draft.soak, 0, 1800)) errs.push(t("queueSched.preheat_card.errSoak"));
    if (!inRange(draft.keepBed, 40, 110)) errs.push(t("queueSched.preheat_card.errBed"));
    if (!inRange(draft.keepMax, 5, 480)) errs.push(t("queueSched.preheat_card.errKeep"));
    setErrors([...new Set(errs)]);
    if (errs.length > 0) return;
    update.mutate(
      {
        enabled: draft.enabled,
        chamber_targets: chamber,
        bed_targets: bed,
        max_wait_s: Number(draft.maxWait),
        soak_s: Number(draft.soak),
        keep_warm_enabled: draft.keepWarm,
        keep_warm_bed_c: Number(draft.keepBed),
        keep_warm_max_min: Number(draft.keepMax),
      },
      { onSuccess: () => setSaved(true) },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("queueSched.preheat_card.title")}</CardTitle>
        <CardDescription>{t("queueSched.preheat_card.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <Switch
            checked={draft.enabled}
            onCheckedChange={(v) => set({ enabled: v })}
            aria-label={t("queueSched.preheat_card.enabled")}
          />
          {t("queueSched.preheat_card.enabled")}
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <RowsEditor
            label={t("queueSched.preheat_card.chamberTargets")}
            rows={draft.chamber}
            onChange={(chamber) => set({ chamber })}
            idPrefix="qp-chamber"
          />
          <RowsEditor
            label={t("queueSched.preheat_card.bedTargets")}
            rows={draft.bed}
            onChange={(bed) => set({ bed })}
            idPrefix="qp-bed"
          />
        </div>
        <div className="flex flex-wrap gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="qp-wait">{t("queueSched.preheat_card.maxWait")}</Label>
            <Input id="qp-wait" type="number" className="w-28" value={draft.maxWait} onChange={(e) => set({ maxWait: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="qp-soak">{t("queueSched.preheat_card.soak")}</Label>
            <Input id="qp-soak" type="number" className="w-28" value={draft.soak} onChange={(e) => set({ soak: e.target.value })} />
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Switch
            checked={draft.keepWarm}
            onCheckedChange={(v) => set({ keepWarm: v })}
            aria-label={t("queueSched.preheat_card.keepWarm")}
          />
          {t("queueSched.preheat_card.keepWarm")}
        </label>
        <div className="flex flex-wrap gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="qp-keep-bed">{t("queueSched.preheat_card.keepWarmBed")}</Label>
            <Input id="qp-keep-bed" type="number" className="w-28" value={draft.keepBed} onChange={(e) => set({ keepBed: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="qp-keep-max">{t("queueSched.preheat_card.keepWarmMax")}</Label>
            <Input id="qp-keep-max" type="number" className="w-28" value={draft.keepMax} onChange={(e) => set({ keepMax: e.target.value })} />
          </div>
        </div>
        {errors.length > 0 ? (
          <ul role="alert" className="space-y-0.5 text-sm text-destructive">
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        ) : null}
        {update.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Couldn't save."}
          </p>
        ) : null}
        <div className="flex items-center gap-3">
          <Button type="button" onClick={save} disabled={update.isPending}>
            {t("queueSched.preheat_card.save")}
          </Button>
          {saved ? <span className="text-sm text-muted-foreground">{t("queueSched.preheat_card.saved")}</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
