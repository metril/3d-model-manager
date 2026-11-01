import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useUpdateServerSettings, type ServerSettingsOut } from "@/api/serverSettings";
import { DisabledReason } from "@/components/settings/server/DisabledReason";
import { Field } from "@/components/settings/server/Field";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

const MASK = "***";
const DOCS_URL = "https://github.com/metril/3d-model-manager/blob/main/docs/orcaslicer.md";

/** OrcaSlicer sidecar (streamed GUI). The launcher token is write-only: "***" = keep. */
export function OrcaSlicerServerCard({ data }: { data: ServerSettingsOut }) {
  const { t } = useTranslation();
  const save = useUpdateServerSettings();
  const c = data.config;
  const hasToken = c.orcaslicer_launcher_token === MASK;
  const [enabled, setEnabled] = useState(c.orcaslicer_enabled);
  const [url, setUrl] = useState(c.orcaslicer_url);
  const [launcherUrl, setLauncherUrl] = useState(c.orcaslicer_launcher_url);
  // Untouched field stays "***" (keep); typing replaces; emptying clears.
  const [token, setToken] = useState(hasToken ? MASK : (c.orcaslicer_launcher_token ?? ""));

  const valid = !enabled || (url.trim() !== "" && launcherUrl.trim() !== "");
  const dirty =
    enabled !== c.orcaslicer_enabled ||
    url.trim() !== c.orcaslicer_url ||
    launcherUrl.trim() !== c.orcaslicer_launcher_url ||
    token !== (c.orcaslicer_launcher_token ?? "");

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.server.orcaslicer.title")}</CardTitle>
        <CardDescription>
          {t("settings.server.orcaslicer.desc")}{" "}
          <a href={DOCS_URL} target="_blank" rel="noreferrer" className="underline">
            {t("settings.server.orcaslicer.docs")}
          </a>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-3">
          <Switch id="srv-orcaslicer-enabled" checked={enabled} onCheckedChange={setEnabled} />
          <Label htmlFor="srv-orcaslicer-enabled">{t("settings.server.orcaslicer.enable")}</Label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="srv-orcaslicer-url" label={t("settings.server.orcaslicer.url")} field="orcaslicer_url" seeded={data.seeded_from_env}>
            <Input id="srv-orcaslicer-url" value={url} placeholder="http://orcaslicer:3000" onChange={(e) => setUrl(e.target.value)} />
          </Field>
          <Field
            id="srv-orcaslicer-launcher-url"
            label={t("settings.server.orcaslicer.launcherUrl")}
            field="orcaslicer_launcher_url"
            seeded={data.seeded_from_env}
          >
            <Input
              id="srv-orcaslicer-launcher-url"
              value={launcherUrl}
              placeholder="http://orcaslicer:3010"
              onChange={(e) => setLauncherUrl(e.target.value)}
            />
          </Field>
          <Field
            id="srv-orcaslicer-launcher-token"
            label={t("settings.server.orcaslicer.launcherToken")}
            field="orcaslicer_launcher_token"
            seeded={data.seeded_from_env}
          >
            <Input
              id="srv-orcaslicer-launcher-token"
              type="password"
              autoComplete="new-password"
              value={token}
              onFocus={() => token === MASK && setToken("")}
              onBlur={() => token === "" && hasToken && setToken(MASK)}
              onChange={(e) => setToken(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {hasToken ? t("settings.server.orcaslicer.tokenKeep") : t("settings.server.orcaslicer.tokenUnset")}
            </p>
          </Field>
        </div>
        {!valid ? (
          <p role="alert" className="text-sm text-destructive">
            {t("settings.server.orcaslicer.invalid")}
          </p>
        ) : null}
        <DisabledReason reason={!dirty ? t("settings.server.noChanges") : null}>
          <Button
            type="button"
            disabled={!valid || !dirty || save.isPending}
            onClick={() =>
              save.mutate(
                {
                  orcaslicer_enabled: enabled,
                  orcaslicer_url: url.trim(),
                  orcaslicer_launcher_url: launcherUrl.trim(),
                  orcaslicer_launcher_token: token === "" ? null : token,
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
