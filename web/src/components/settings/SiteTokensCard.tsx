import { Trans, useTranslation } from "react-i18next";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useImportTokens, useUpdateImportTokens } from "@/api/imports";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

export function SiteTokensCard() {
  const { t } = useTranslation();
  const tokens = useImportTokens();
  const update = useUpdateImportTokens();
  const [thingiverseToken, setThingiverseToken] = useState("");
  const [makerworldToken, setMakerworldToken] = useState("");

  if (tokens.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  const isThingiverseSet = tokens.data?.thingiverse_token === "***";
  const isMakerworldSet = tokens.data?.makerworld_token === "***";

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsSiteTokens.title")}</CardTitle>
        <CardDescription>
          {t("settingsSiteTokens.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="thingiverse-token">{t("settingsSiteTokens.thingiverseLabel")}</Label>
          <Input
            id="thingiverse-token"
            type="password"
            autoComplete="new-password"
            value={thingiverseToken}
            placeholder={isThingiverseSet ? t("settingsSiteTokens.stored") : t("settingsSiteTokens.pasteApp")}
            onChange={(e) => setThingiverseToken(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="makerworld-token">{t("settingsSiteTokens.makerworldLabel")}</Label>
          <Input
            id="makerworld-token"
            type="password"
            autoComplete="new-password"
            value={makerworldToken}
            placeholder={isMakerworldSet ? t("settingsSiteTokens.stored") : t("settingsSiteTokens.pasteWeb")}
            onChange={(e) => setMakerworldToken(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            <Trans i18nKey="settingsSiteTokens.makerworldHelp" components={{ code: <code /> }} />
          </p>
        </div>
        <Button
          type="button"
          disabled={update.isPending}
          onClick={() =>
            update.mutate(
              { thingiverse_token: thingiverseToken, makerworld_token: makerworldToken },
              {
                onSuccess: () => {
                  setThingiverseToken("");
                  setMakerworldToken("");
                },
              },
            )
          }
        >
          {update.isPending ? t("settingsSiteTokens.saving") : t("settingsSiteTokens.save")}
        </Button>
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsSiteTokens.saveError")}
          </p>
        )}
        {update.isSuccess && <p className="text-sm text-success">{t("settingsSiteTokens.saved")}</p>}
      </CardContent>
    </Card>
  );
}
