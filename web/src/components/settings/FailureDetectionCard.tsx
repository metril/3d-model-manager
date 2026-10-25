/** Settings → Printer → Failure detection (Q10 Obico ML sidecar). The global
 * switch, thresholds and sidecar URLs live in Settings → Server; per-printer
 * overrides stay in Printer setup. This card is the pointer + current state. */
import { Link } from "@tanstack/react-router";
import { Trans, useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { useObicoSettings } from "@/api/detection";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function FailureDetectionCard() {
  const { t } = useTranslation();
  const isAdmin = useAuth().can("admin");
  const q = useObicoSettings();
  if (q.isLoading) return <Skeleton className="h-24 w-full rounded-xl" />;
  const s = q.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t("settings.server.obico.detectionTitle")}
          {s ? (
            <Badge variant={s.enabled ? "secondary" : "outline"}>
              {s.enabled ? t("settings.server.obico.on") : t("settings.server.obico.off")}
            </Badge>
          ) : null}
        </CardTitle>
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
      {s ? (
        <CardContent className="text-sm text-muted-foreground">
          {t("settings.server.obico.summary", {
            action: s.action,
            warn: s.warn,
            pause: s.pause,
            interval: s.interval_s,
          })}
        </CardContent>
      ) : null}
    </Card>
  );
}
