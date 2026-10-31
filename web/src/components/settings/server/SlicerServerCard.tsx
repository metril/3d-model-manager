import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import {
  useServerStatus,
  useTestSlicer,
  useUpdateServerSettings,
  type ServerSettingsOut,
  type SlicerProbeInfo,
} from "@/api/serverSettings";
import { DisabledReason } from "@/components/settings/server/DisabledReason";
import { Field } from "@/components/settings/server/Field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { humanizeBytes } from "@/lib/format";

function SlicerInfo({ info, appTimeoutS }: { info: SlicerProbeInfo | null | undefined; appTimeoutS: number }) {
  const { t } = useTranslation();
  if (!info) return null;
  const max = info.max_concurrency;
  const running = info.running ?? (info.busy ? (max ?? 1) : 0);
  const tooShort = info.job_timeout_s != null && info.job_timeout_s < appTimeoutS;
  return (
    <div className="space-y-2 rounded-md border p-3 text-sm" data-testid="slicer-info">
      <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
        {info.version ? (
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">{t("settings.server.slicer.version")}</dt>
            <dd>{info.version}</dd>
          </div>
        ) : null}
        {info.slicer_version ? (
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">{t("settings.server.slicer.slicerVersion")}</dt>
            <dd>{info.slicer_version}</dd>
          </div>
        ) : null}
        {max != null ? (
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">{t("settings.server.slicer.slots")}</dt>
            <dd>
              {running}/{max} {running >= max ? t("settings.server.slicer.busy") : t("settings.server.slicer.idle")}
            </dd>
          </div>
        ) : null}
        {info.job_timeout_s != null ? (
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">{t("settings.server.slicer.jobTimeout")}</dt>
            <dd>{info.job_timeout_s}s</dd>
          </div>
        ) : null}
        <div className="flex justify-between gap-2">
          <dt className="text-muted-foreground">{t("settings.server.slicer.memLimit")}</dt>
          <dd>{info.mem_limit != null ? humanizeBytes(info.mem_limit) : t("settings.server.slicer.unlimited")}</dd>
        </div>
      </dl>
      {tooShort ? (
        <p role="alert" className="text-destructive">
          {t("settings.server.slicer.timeoutWarn", { sidecar: info.job_timeout_s, app: appTimeoutS })}
        </p>
      ) : null}
    </div>
  );
}

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
        <SlicerInfo info={status.data?.slicer.info} appTimeoutS={valid ? tN : c.slice_timeout_s} />
        <div className="flex items-center gap-3">
          <Switch id="srv-slicer-enabled" checked={enabled} onCheckedChange={setEnabled} />
          <Label htmlFor="srv-slicer-enabled">{t("settings.server.slicer.enable")}</Label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="srv-slicer-url" label={t("settings.server.slicer.url")} field="slicer_url" seeded={data.seeded_from_env} className="sm:col-span-2">
            <div className="flex gap-2">
              <Input id="srv-slicer-url" value={url} placeholder="http://slicer:3001" onChange={(e) => setUrl(e.target.value)} />
              <DisabledReason reason={url.trim() === "" ? t("settings.server.slicer.needUrl") : null}>
                <Button type="button" variant="outline" disabled={test.isPending || url.trim() === ""} onClick={runTest}>
                  {t("settings.server.slicer.test")}
                </Button>
              </DisabledReason>
            </div>
          </Field>
          <Field id="srv-slice-timeout" label={t("settings.server.slicer.timeout")} field="slice_timeout_s" seeded={data.seeded_from_env}>
            <Input id="srv-slice-timeout" type="number" min={30} max={7200} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} />
          </Field>
        </div>
        {!valid ? (
          <p role="alert" className="text-sm text-destructive">{t("settings.server.slicer.invalid")}</p>
        ) : null}
        <DisabledReason reason={!dirty ? t("settings.server.noChanges") : null}>
        <Button
          type="button"
          disabled={!valid || !dirty || save.isPending}
          onClick={() =>
            save.mutate(
              { slicer_enabled: enabled, slicer_url: url.trim(), slice_timeout_s: tN },
              {
                onSuccess: () => toast.success(t("settings.server.saved")),
                onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.saveError")),
              },
            )
          }
        >
          {t("settings.server.save")}
        </Button>
        </DisabledReason>
      </CardContent>
    </Card>
  );
}
