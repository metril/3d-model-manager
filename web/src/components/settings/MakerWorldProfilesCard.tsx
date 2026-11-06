/** Settings -> Integrations -> default MakerWorld print-profile filter for imports. */
import { useTranslation } from "react-i18next";

import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import { ApiError } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";

export function MakerWorldProfilesCard() {
  const { t } = useTranslation();
  const settings = useAppSettings();
  const update = useUpdateAppSettings();
  if (settings.isLoading) return <Skeleton className="h-32 w-full rounded-xl" />;
  if (!settings.data) return null;
  const initial = settings.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsMakerworld.title")}</CardTitle>
        <CardDescription>
          {t("settingsMakerworld.desc")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <SwitchRow
          id="makerworld-owned-profiles"
          label={t("settingsMakerworld.ownedLabel")}
          description={t("settingsMakerworld.ownedDesc")}
          checked={initial.makerworld_profiles === "owned"}
          disabled={update.isPending}
          onCheckedChange={(on) =>
            update.mutate({ ...initial, makerworld_profiles: on ? "owned" : "all" })
          }
        />
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsMakerworld.saveError")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
