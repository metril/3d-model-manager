import { Trans, useTranslation } from "react-i18next";
import { useAuth } from "@/api/auth";
import { useOpsSettings, useSaveOpsSettings, useUpdateInfo } from "@/api/ops";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SwitchRow } from "@/components/ui/switch-row";

export function UpdatesCard() {
  const { t } = useTranslation();
  const auth = useAuth();
  const isAdmin = auth.can("admin");
  const info = useUpdateInfo();
  const ops = useOpsSettings();
  const save = useSaveOpsSettings();
  const u = info.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsUpdates.title")}</CardTitle>
        <CardDescription>{t("settingsUpdates.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {u ? (
          <>
            <p className="text-sm">
              <Trans
                i18nKey="settingsUpdates.versions"
                components={{ cur: <span className="font-mono" />, latest: <span className="font-mono" /> }}
                values={{ cur: u.current, latest: u.latest ?? t("settingsUpdates.unknown") }}
              />
            </p>
            {u.update_available ? (
              <p className="text-sm font-medium text-primary">
                {t("settingsUpdates.available")}{" "}
                {u.url ? (
                  <a href={u.url} target="_blank" rel="noreferrer" className="underline">
                    {t("settingsUpdates.releaseNotes")}
                  </a>
                ) : null}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">{t("settingsUpdates.upToDate")}</p>
            )}
            {u.error ? (
              <p role="alert" className="text-sm text-destructive">
                {u.error}
              </p>
            ) : null}
          </>
        ) : info.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {t("settingsUpdates.checkError")}
          </p>
        ) : null}
        {ops.data ? (
          <SwitchRow
            id="upd-enabled"
            label={t("settingsUpdates.checkLabel")}
            checked={ops.data.update_check_enabled}
            onCheckedChange={(v) => save.mutate({ update_check_enabled: v })}
          />
        ) : null}
        {isAdmin ? (
          <Button asChild variant="outline" size="sm">
            <a href="/api/admin/support-bundle" download>
              {t("settingsUpdates.supportBundle")}
            </a>
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
