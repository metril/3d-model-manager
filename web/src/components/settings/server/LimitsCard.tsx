import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useUpdateServerSettings, type LogLevel, type ServerSettingsOut } from "@/api/serverSettings";
import { DisabledReason } from "@/components/settings/server/DisabledReason";
import { Field } from "@/components/settings/server/Field";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const MB = 1024 * 1024;
const LEVELS: LogLevel[] = ["DEBUG", "INFO", "WARNING", "ERROR"];
const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export function LimitsCard({ data }: { data: ServerSettingsOut }) {
  const { t } = useTranslation();
  const save = useUpdateServerSettings();
  const c = data.config;
  const [up, setUp] = useState(String(Math.round(c.max_upload_bytes / MB)));
  const [down, setDown] = useState(String(Math.round(c.max_download_bytes / MB)));
  const [hb, setHb] = useState(String(c.sse_heartbeat_s));
  const [level, setLevel] = useState<LogLevel>(c.log_level);

  const upN = Number(up);
  const downN = Number(down);
  const hbN = Number(hb);
  const valid =
    Number.isInteger(upN) && upN >= 1 && Number.isInteger(downN) && downN >= 1 && Number.isInteger(hbN) && hbN >= 5 && hbN <= 120;
  const bytes = (n: number, orig: number) => (Math.round(orig / MB) === n ? orig : n * MB);
  const dirty =
    bytes(upN, c.max_upload_bytes) !== c.max_upload_bytes ||
    bytes(downN, c.max_download_bytes) !== c.max_download_bytes ||
    hbN !== c.sse_heartbeat_s ||
    level !== c.log_level;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.server.limits.title")}</CardTitle>
        <CardDescription>{t("settings.server.limits.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="srv-upload" label={t("settings.server.limits.upload")} field="max_upload_bytes" seeded={data.seeded_from_env}>
            <Input id="srv-upload" type="number" min={1} value={up} onChange={(e) => setUp(e.target.value)} />
          </Field>
          <Field id="srv-download" label={t("settings.server.limits.download")} field="max_download_bytes" seeded={data.seeded_from_env}>
            <Input id="srv-download" type="number" min={1} value={down} onChange={(e) => setDown(e.target.value)} />
          </Field>
          <Field id="srv-heartbeat" label={t("settings.server.limits.heartbeat")} field="sse_heartbeat_s" seeded={data.seeded_from_env}>
            <Input id="srv-heartbeat" type="number" min={5} max={120} value={hb} onChange={(e) => setHb(e.target.value)} />
          </Field>
          <Field id="srv-log-level" label={t("settings.server.limits.logLevel")} field="log_level" seeded={data.seeded_from_env}>
            <select id="srv-log-level" className={selectClass} value={level} onChange={(e) => setLevel(e.target.value as LogLevel)}>
              {LEVELS.map((l) => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </Field>
        </div>
        {!valid ? (
          <p role="alert" className="text-sm text-destructive">{t("settings.server.limits.invalid")}</p>
        ) : null}
        <DisabledReason reason={!dirty ? t("settings.server.noChanges") : null}>
        <Button
          type="button"
          disabled={!valid || !dirty || save.isPending}
          onClick={() =>
            save.mutate(
              {
                max_upload_bytes: bytes(upN, c.max_upload_bytes),
                max_download_bytes: bytes(downN, c.max_download_bytes),
                sse_heartbeat_s: hbN,
                log_level: level,
              },
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
