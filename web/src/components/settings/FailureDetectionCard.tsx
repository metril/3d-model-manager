/** Settings → Printer → Failure detection (Q10 Obico ML sidecar). */
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import type { DetectionAction, ObicoSettings } from "@/api/detection";
import { useObicoSettings, useSaveObicoSettings } from "@/api/detection";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export function FailureDetectionCard() {
  const q = useObicoSettings();
  if (q.isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;
  if (!q.data) return null;
  return <Form initial={q.data} />;
}

function Form({ initial }: { initial: ObicoSettings }) {
  const { t } = useTranslation();
  const isAdmin = useAuth().can("admin");
  const [draft, setDraft] = useState<ObicoSettings>(initial);
  const save = useSaveObicoSettings();
  const set = <K extends keyof ObicoSettings>(k: K, v: ObicoSettings[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));
  const invalid = draft.pause < draft.warn;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Failure detection</CardTitle>
        <CardDescription>
          {isAdmin ? (
            <Trans
              i18nKey="settings.server.obico.detectionDesc"
              components={{ 1: <Link to="/settings" search={{ tab: "server" }} className="underline" /> }}
            />
          ) : (
            t("settings.server.obico.detectionDescPlain")
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-3">
          <Switch id="obico-enabled" checked={draft.enabled} onCheckedChange={(v) => set("enabled", v)} />
          <Label htmlFor="obico-enabled">Enable failure detection</Label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="obico-interval">Check every (seconds)</Label>
            <Input
              id="obico-interval"
              type="number"
              min={2}
              max={300}
              value={draft.interval_s}
              onChange={(e) => set("interval_s", Number(e.target.value))}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="obico-action">Default action</Label>
            <select
              id="obico-action"
              className={selectClass}
              value={draft.action}
              onChange={(e) => set("action", e.target.value as DetectionAction)}
            >
              <option value="none">Record only</option>
              <option value="notify">Notify</option>
              <option value="pause">Pause the print</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="obico-warn">Warn threshold</Label>
            <Input
              id="obico-warn"
              type="number"
              step={0.01}
              min={0}
              value={draft.warn}
              onChange={(e) => set("warn", Number(e.target.value))}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="obico-pause">Pause threshold</Label>
            <Input
              id="obico-pause"
              type="number"
              step={0.01}
              min={0}
              value={draft.pause}
              onChange={(e) => set("pause", Number(e.target.value))}
            />
          </div>
        </div>
        {invalid ? (
          <p role="alert" className="text-sm text-destructive">
            The pause threshold must be at least the warn threshold.
          </p>
        ) : null}
        {save.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {save.error instanceof ApiError ? save.error.detail : "Could not save."}
          </p>
        ) : null}
        <Button type="button" disabled={invalid || save.isPending} onClick={() => save.mutate(draft)}>
          Save
        </Button>
      </CardContent>
    </Card>
  );
}
