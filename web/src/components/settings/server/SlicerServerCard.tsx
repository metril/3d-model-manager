import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import {
  useServerStatus,
  useTestSlicer,
  useUpdateServerSettings,
  type ServerSettingsOut,
} from "@/api/serverSettings";
import { Field } from "@/components/settings/server/Field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

export function SlicerServerCard({ data }: { data: ServerSettingsOut }) {
  const { t } = useTranslation();
  const save = useUpdateServerSettings();
  const test = useTestSlicer();
  const status = useServerStatus();
  const c = data.config;
  const [enabled, setEnabled] = useState(c.slicer_enabled);
  const [url, setUrl] = useState(c.slicer_url);
  const [timeout, setTimeoutS] = useState(String(c.slice_timeout_s));

  const tN = Number(timeout);
  const valid = Number.isInteger(tN) && tN >= 30 && tN <= 7200 && (!enabled || url.trim() !== "");
  const dirty = enabled !== c.slicer_enabled || url.trim() !== c.slicer_url || tN !== c.slice_timeout_s;
  const healthy = status.data?.slicer_healthy;

  const runTest = () =>
    test.mutate(url.trim(), {
      onSuccess: (r) =>
        r.ok
          ? toast.success(t("settings.server.slicer.testOk", { ms: r.latency_ms ?? 0 }))
          : toast.error(t("settings.server.slicer.testFail", { detail: r.detail })),
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.slicer.testError")),
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t("settings.server.slicer.title")}
          {healthy === true ? (
            <Badge variant="secondary">{t("settings.server.slicer.healthy")}</Badge>
          ) : healthy === false ? (
            <Badge variant="destructive">{t("settings.server.slicer.unreachable")}</Badge>
          ) : (
            <Badge variant="outline">{t("settings.server.slicer.unchecked")}</Badge>
          )}
        </CardTitle>
        <CardDescription>{t("settings.server.slicer.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-3">
          <Switch id="srv-slicer-enabled" checked={enabled} onCheckedChange={setEnabled} />
          <Label htmlFor="srv-slicer-enabled">{t("settings.server.slicer.enable")}</Label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="srv-slicer-url" label={t("settings.server.slicer.url")} field="slicer_url" seeded={data.seeded_from_env} className="sm:col-span-2">
            <div className="flex gap-2">
              <Input id="srv-slicer-url" value={url} placeholder="http://slicer:3001" onChange={(e) => setUrl(e.target.value)} />
              <Button
                type="button"
                variant="outline"
                disabled={test.isPending || url.trim() === ""}
                title={url.trim() === "" ? t("settings.server.slicer.needUrl") : undefined}
                onClick={runTest}
              >
                {t("settings.server.slicer.test")}
              </Button>
            </div>
          </Field>
          <Field id="srv-slice-timeout" label={t("settings.server.slicer.timeout")} field="slice_timeout_s" seeded={data.seeded_from_env}>
            <Input id="srv-slice-timeout" type="number" min={30} max={7200} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} />
          </Field>
        </div>
        {!valid ? (
          <p role="alert" className="text-sm text-destructive">{t("settings.server.slicer.invalid")}</p>
        ) : null}
        <Button
          type="button"
          disabled={!valid || !dirty || save.isPending}
          title={!dirty ? t("settings.server.noChanges") : undefined}
          onClick={() =>
            save.mutate(
              { ...c, slicer_enabled: enabled, slicer_url: url.trim(), slice_timeout_s: tN },
              {
                onSuccess: () => toast.success(t("settings.server.saved")),
                onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.saveError")),
              },
            )
          }
        >
          {t("settings.server.save")}
        </Button>
      </CardContent>
    </Card>
  );
}
