/** Settings -> Storage -> how long trashed models are kept before the daily auto-purge. */
import { useTranslation } from "react-i18next";
import { useState } from "react";

import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import { ApiError } from "@/api/client";
import type { AppSettings } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

export function TrashRetentionCard() {
  const settings = useAppSettings();
  if (settings.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  if (!settings.data) return null;
  return <Form initial={settings.data} />;
}

function Form({ initial }: { initial: AppSettings }) {
  const { t } = useTranslation();
  const update = useUpdateAppSettings();
  const [days, setDays] = useState(String(initial.trash_retention_days));
  const n = Number(days);
  const invalid = days.trim() === "" || !Number.isInteger(n) || n < 0 || n > 3650;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsTrash.title")}</CardTitle>
        <CardDescription>
          {t("settingsTrash.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="trash-retention">{t("settingsTrash.label")}</Label>
          <Input
            id="trash-retention"
            type="number"
            min={0}
            max={3650}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            aria-invalid={invalid}
            className="w-32"
          />
          {invalid ? (
            <p role="alert" className="text-xs text-destructive">
              {t("settingsTrash.invalid")}
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          <Button
            type="button"
            disabled={invalid || update.isPending}
            onClick={() => update.mutate({ ...initial, trash_retention_days: n })}
          >
            {update.isPending ? t("settingsTrash.saving") : t("common.save")}
          </Button>
          {update.isSuccess && <p className="text-sm text-success">{t("settingsTrash.saved")}</p>}
        </div>
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsTrash.saveError")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
