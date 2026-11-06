/**
 * Automation & scheduling card (Round 10 T5): the General tab's editor for
 * the four background-schedule intervals in the DB-backed `AppSettings`
 * (`GET`/`PUT /settings/app`) -- library scan, collection sync, and the
 * watched-folder poll + stability delay. Mirrors `SiteTokensCard`'s
 * draft/save/alert/"Saved." card conventions.
 *
 * `printer_enabled` isn't edited here (see `PrinterEnabledCard`), but it IS
 * included in every `PUT` body below -- the endpoint is a full replace of
 * all five `AppSettings` fields, not a per-field patch.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import type { TFunction } from "i18next";

import { ApiError } from "@/api/client";
import { useFeatures } from "@/api/features";
import type { AppSettings } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

interface Draft {
  scan_interval_s: string;
  collection_sync_interval_s: string;
  watch_interval_s: string;
  watch_stable_s: string;
}

type DraftErrors = Partial<Record<keyof Draft, string>>;

// `scan_interval_s`/`collection_sync_interval_s`/`watch_interval_s` are all
// server-side `int` fields (`ge=0`); `watch_stable_s` is the one `float`
// field (also `ge=0`) -- a fractional value is fine there but would 422 on
// the other three.
const INT_FIELDS: readonly (keyof Draft)[] = [
  "scan_interval_s",
  "collection_sync_interval_s",
  "watch_interval_s",
];

function seedDraft(settings: AppSettings): Draft {
  return {
    scan_interval_s: String(settings.scan_interval_s),
    collection_sync_interval_s: String(settings.collection_sync_interval_s),
    watch_interval_s: String(settings.watch_interval_s),
    watch_stable_s: String(settings.watch_stable_s),
  };
}

/** M2: `Number("")` is `0`, so a cleared field used to silently turn a
 * schedule OFF on save with no confirmation; a non-integer typed into an
 * int field became a float that 422s server-side. Both are caught here,
 * client-side, before the request ever goes out. */
function validateField(t: TFunction, key: keyof Draft, raw: string): string | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return t("settingsAutomation.required");
  const n = Number(trimmed);
  if (Number.isNaN(n)) return t("settingsAutomation.nan");
  if (n < 0) return t("settingsAutomation.negative");
  if (INT_FIELDS.includes(key) && !Number.isInteger(n)) return t("settingsAutomation.notInt");
  return undefined;
}

export function AutomationCard() {
  const { t } = useTranslation();
  const settings = useAppSettings();
  const features = useFeatures();
  const update = useUpdateAppSettings();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<DraftErrors>({});

  useEffect(() => {
    if (settings.data && draft === null) setDraft(seedDraft(settings.data));
  }, [settings.data, draft]);

  if (settings.isLoading || draft === null) return <Skeleton className="h-80 w-full rounded-xl" />;

  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("settingsAutomation.title")}</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : t("settingsAutomation.loadError")}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  function setField(key: keyof Draft, value: string) {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
    setErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
  }

  function validate(current: Draft): boolean {
    const next: DraftErrors = {};
    (Object.keys(current) as (keyof Draft)[]).forEach((key) => {
      const message = validateField(t, key, current[key]);
      if (message) next[key] = message;
    });
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function save() {
    if (!settings.data || !draft) return;
    if (!validate(draft)) return;
    update.mutate({
      ...settings.data,
      scan_interval_s: Number(draft.scan_interval_s),
      collection_sync_interval_s: Number(draft.collection_sync_interval_s),
      watch_interval_s: Number(draft.watch_interval_s),
      watch_stable_s: Number(draft.watch_stable_s),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsAutomation.title")}</CardTitle>
        <CardDescription>
          {t("settingsAutomation.desc")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="scan-interval">{t("settingsAutomation.scanLabel")}</Label>
            <Input
              id="scan-interval"
              type="number"
              min={0}
              value={draft.scan_interval_s}
              onChange={(e) => setField("scan_interval_s", e.target.value)}
              aria-invalid={Boolean(errors.scan_interval_s)}
            />
            {errors.scan_interval_s ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.scan_interval_s}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("settingsAutomation.scanHint")}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="collection-sync-interval">{t("settingsAutomation.syncLabel")}</Label>
            <Input
              id="collection-sync-interval"
              type="number"
              min={0}
              value={draft.collection_sync_interval_s}
              onChange={(e) => setField("collection_sync_interval_s", e.target.value)}
              aria-invalid={Boolean(errors.collection_sync_interval_s)}
            />
            {errors.collection_sync_interval_s ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.collection_sync_interval_s}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("settingsAutomation.syncHint")}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="watch-interval">{t("settingsAutomation.watchLabel")}</Label>
            <Input
              id="watch-interval"
              type="number"
              min={0}
              value={draft.watch_interval_s}
              onChange={(e) => setField("watch_interval_s", e.target.value)}
              aria-invalid={Boolean(errors.watch_interval_s)}
            />
            {errors.watch_interval_s ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.watch_interval_s}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("settingsAutomation.watchHint")}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="watch-stable">{t("settingsAutomation.stableLabel")}</Label>
            <Input
              id="watch-stable"
              type="number"
              min={0}
              step="any"
              value={draft.watch_stable_s}
              onChange={(e) => setField("watch_stable_s", e.target.value)}
              aria-invalid={Boolean(errors.watch_stable_s)}
            />
            {errors.watch_stable_s ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.watch_stable_s}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("settingsAutomation.stableHint")}
              </p>
            )}
          </div>
        </div>

        <p className="text-sm text-muted-foreground">
          {features.data?.watch_dir
            ? t("settingsAutomation.watchDir", { dir: features.data.watch_dir })
            : t("settingsAutomation.noWatchDir")}
        </p>

        <div className="flex items-center gap-3">
          <Button type="button" disabled={update.isPending} onClick={save}>
            {update.isPending ? t("settingsAutomation.saving") : t("common.save")}
          </Button>
          {update.isSuccess && <p className="text-sm text-success">{t("settingsAutomation.saved")}</p>}
        </div>
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsAutomation.saveError")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
