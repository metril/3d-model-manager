/**
 * Spoolman connector card (Q9): server URL, sync mode (off / mirror /
 * authoritative), Test connection and Sync now.
 */
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type SpoolmanMode,
  useSpoolmanSettings,
  useSpoolmanSync,
  useSpoolmanTest,
  useUpdateSpoolmanSettings,
} from "@/api/spoolman";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

export function SpoolmanCard() {
  const { t } = useTranslation();
  const MODE_HELP: Record<SpoolmanMode, string> = {
    off: t("settingsSpoolman.helpOff"),
    mirror: t("settingsSpoolman.helpMirror"),
    authoritative: t("settingsSpoolman.helpAuthoritative"),
  };
  const settings = useSpoolmanSettings();
  const update = useUpdateSpoolmanSettings();
  const test = useSpoolmanTest();
  const sync = useSpoolmanSync();
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<SpoolmanMode>("off");
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    if (settings.data && !seeded) {
      setUrl(settings.data.url ?? "");
      setMode(settings.data.mode);
      setSeeded(true);
    }
  }, [settings.data, seeded]);

  if (settings.isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;
  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("settingsSpoolman.title")}</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : t("settingsSpoolman.loadError")}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const trimmed = url.trim();
  const saved = settings.data;
  const dirty = trimmed !== (saved.url ?? "") || mode !== saved.mode;
  const enabled = saved.mode !== "off" && !!saved.url;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsSpoolman.title")}</CardTitle>
        <CardDescription>
          {t("settingsSpoolman.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="spoolman-url">{t("settingsSpoolman.serverUrl")}</Label>
            <Input
              id="spoolman-url"
              placeholder="http://spoolman.local:7912"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="spoolman-mode">{t("settingsSpoolman.mode")}</Label>
            <select
              id="spoolman-mode"
              className="h-9 rounded-md border border-input bg-background ps-3 text-sm"
              value={mode}
              onChange={(e) => setMode(e.target.value as SpoolmanMode)}
            >
              <option value="off">{t("settingsSpoolman.modeOff")}</option>
              <option value="mirror">{t("settingsSpoolman.modeMirror")}</option>
              <option value="authoritative">{t("settingsSpoolman.modeAuthoritative")}</option>
            </select>
            <p className="text-xs text-muted-foreground">{MODE_HELP[mode]}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            disabled={update.isPending || !dirty}
            onClick={() => update.mutate({ url: trimmed || null, mode })}
          >
            {update.isPending ? t("settingsSpoolman.saving") : t("common.save")}
          </Button>
          <Button type="button" variant="outline" disabled={!trimmed || test.isPending} onClick={() => test.mutate(trimmed)}>
            {t("settingsSpoolman.test")}
          </Button>
          <Button type="button" variant="outline" disabled={!enabled || sync.isPending} onClick={() => sync.mutate()}>
            {sync.isPending ? t("settingsSpoolman.syncing") : t("settingsSpoolman.syncNow")}
          </Button>
        </div>

        {update.isSuccess && !dirty && <p className="text-sm text-success">{t("settingsSpoolman.saved")}</p>}
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsSpoolman.saveError")}
          </p>
        )}
        {test.data &&
          (test.data.ok ? (
            <p className="text-sm text-success">
              {test.data.version
                ? t("settingsSpoolman.connectedVersion", { version: test.data.version })
                : t("settingsSpoolman.connected")}
            </p>
          ) : (
            <p role="alert" className="text-sm text-destructive">
              {t("settingsSpoolman.connectionFailed", { error: test.data.error })}
            </p>
          ))}
        {sync.data && (
          <p className="text-sm text-muted-foreground">
            {t("settingsSpoolman.synced", {
              pulled: sync.data.pulled,
              errors: sync.data.errors ? t("settingsSpoolman.syncedErrors", { errors: sync.data.errors }) : "",
            })}
          </p>
        )}
        {sync.isError && (
          <p role="alert" className="text-sm text-destructive">
            {sync.error instanceof ApiError ? sync.error.detail : t("settingsSpoolman.syncError")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
