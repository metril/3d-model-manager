import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { adminStatusQueryOptions } from "@/api/adminStatus";
import { ApiError } from "@/api/client";
import {
  useObicoSettings,
  useSaveObicoSettings,
  useTestObico,
  type DetectionAction,
  type ObicoSettings,
} from "@/api/detection";
import { useServerStatus } from "@/api/serverSettings";
import { DisabledReason } from "@/components/settings/server/DisabledReason";
import { Field } from "@/components/settings/server/Field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export function ObicoServerCard() {
  const q = useObicoSettings();
  if (q.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  if (!q.data) return null;
  return <Form initial={q.data} />;
}

function Form({ initial }: { initial: ObicoSettings }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const save = useSaveObicoSettings();
  const test = useTestObico();
  const status = useServerStatus();
  const [url, setUrl] = useState(initial.url);
  const [cb, setCb] = useState(initial.callback_url);
  const [enabled, setEnabled] = useState(initial.enabled);
  const [interval, setIntervalS] = useState(String(initial.interval_s));
  const [warn, setWarn] = useState(String(initial.warn));
  const [pause, setPause] = useState(String(initial.pause));
  const [action, setAction] = useState<DetectionAction>(initial.action);

  const iN = Number(interval);
  const wN = Number(warn);
  const pN = Number(pause);
  const errKey =
    !Number.isInteger(iN) || iN < 2 || iN > 300
      ? "errInterval"
      : !(wN >= 0 && wN <= 5 && pN >= 0 && pN <= 5)
        ? "errRange"
        : pN < wN
          ? "errOrder"
          : url.trim() === "" && enabled
            ? "errUrl"
            : null;
  const dirty =
    url.trim() !== initial.url ||
    cb.trim() !== initial.callback_url ||
    enabled !== initial.enabled ||
    iN !== initial.interval_s ||
    wN !== initial.warn ||
    pN !== initial.pause ||
    action !== initial.action;
  const ok = status.data?.obico.ok;
  const seeded = initial.seeded_from_env ?? {};
  const disabledReason = errKey ? t(`settings.server.obico.${errKey}`) : !dirty ? t("settings.server.noChanges") : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t("settings.server.obico.title")}
          {ok === undefined ? null : ok ? (
            <Badge variant="secondary">{t("settings.server.obico.healthy")}</Badge>
          ) : (
            <Badge variant="destructive">{t("settings.server.obico.unreachable")}</Badge>
          )}
        </CardTitle>
        <CardDescription>{t("settings.server.obico.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-3">
          <Switch id="srv-obico-enabled" checked={enabled} onCheckedChange={setEnabled} />
          <Label htmlFor="srv-obico-enabled">{t("settings.server.obico.enable")}</Label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="srv-obico-url" label={t("settings.server.obico.url")} field="url" seeded={seeded}>
            <Input id="srv-obico-url" value={url} placeholder="http://obico-ml:3333" onChange={(e) => setUrl(e.target.value)} />
          </Field>
          <Field id="srv-obico-cb" label={t("settings.server.obico.callback")} field="callback_url" seeded={seeded}>
            <Input id="srv-obico-cb" value={cb} placeholder="http://api:8080" onChange={(e) => setCb(e.target.value)} />
          </Field>
          <Field id="srv-obico-interval" label={t("settings.server.obico.interval")} seeded={seeded}>
            <Input id="srv-obico-interval" type="number" min={2} max={300} value={interval} onChange={(e) => setIntervalS(e.target.value)} />
          </Field>
          <Field id="srv-obico-action" label={t("settings.server.obico.action")} seeded={seeded}>
            <select id="srv-obico-action" className={selectClass} value={action} onChange={(e) => setAction(e.target.value as DetectionAction)}>
              <option value="none">{t("settings.server.obico.actionNone")}</option>
              <option value="notify">{t("settings.server.obico.actionNotify")}</option>
              <option value="pause">{t("settings.server.obico.actionPause")}</option>
            </select>
          </Field>
          <Field id="srv-obico-warn" label={t("settings.server.obico.warn")} seeded={seeded}>
            <Input id="srv-obico-warn" type="number" step={0.01} min={0} value={warn} onChange={(e) => setWarn(e.target.value)} />
          </Field>
          <Field id="srv-obico-pause" label={t("settings.server.obico.pause")} seeded={seeded}>
            <Input id="srv-obico-pause" type="number" step={0.01} min={0} value={pause} onChange={(e) => setPause(e.target.value)} />
          </Field>
        </div>
        {errKey ? (
          <p role="alert" className="text-sm text-destructive">
            {t(`settings.server.obico.${errKey}`)}
          </p>
        ) : null}

        <p className="text-xs text-muted-foreground">
          {t("settings.server.obico.mlVersion")}:{" "}
          {initial.ml_version ? (
            <span className="font-mono">
              {initial.ml_version.slice(0, 12)}
              {initial.ml_version_source ? ` (${t(`settings.server.obico.src_${initial.ml_version_source}`)})` : null}
            </span>
          ) : (
            t("settings.server.obico.mlVersionUnknown")
          )}
        </p>

        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <DisabledReason reason={url.trim() === "" ? t("settings.server.obico.needUrl") : null}>
              <Button
                type="button"
                variant="outline"
                disabled={test.isPending || url.trim() === ""}
                onClick={() => test.mutate({ url: url.trim(), callback_url: cb.trim() || undefined })}
              >
                {test.isPending ? t("settings.server.obico.testing") : t("settings.server.obico.test")}
              </Button>
            </DisabledReason>
            <span className="text-xs text-muted-foreground">{t("settings.server.obico.testHint")}</span>
          </div>
          {test.isError ? (
            <p role="alert" className="text-xs text-destructive">
              {test.error instanceof ApiError ? test.error.detail : t("settings.server.obico.testError")}
            </p>
          ) : null}
          {test.data ? (
            <ul aria-label={t("settings.server.obico.checks")} className="space-y-1 text-sm">
              {test.data.checks.map((c) => (
                <li key={c.name} className="flex items-start gap-2">
                  <Badge variant={c.ok ? "secondary" : "destructive"}>
                    {c.ok ? t("settings.server.obico.pass") : t("settings.server.obico.fail")}
                  </Badge>
                  <span>
                    <span className="font-medium">{t(`settings.server.obico.check_${c.name}`)}</span>
                    <span className="text-muted-foreground"> {c.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        <DisabledReason reason={disabledReason}>
          <Button
            type="button"
            disabled={!!disabledReason || save.isPending}
            onClick={() =>
              save.mutate(
                {
                  url: url.trim(),
                  callback_url: cb.trim(),
                  enabled,
                  interval_s: iN,
                  warn: wN,
                  pause: pN,
                  action,
                },
                {
                  onSuccess: () => {
                    toast.success(t("settings.server.saved"));
                    void qc.invalidateQueries({ queryKey: adminStatusQueryOptions.queryKey });
                  },
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
