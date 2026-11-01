import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useServerSettings } from "@/api/serverSettings";
import { LimitsCard } from "@/components/settings/server/LimitsCard";
import { LogsCard } from "@/components/settings/server/LogsCard";
import { MqttRelayCard } from "@/components/settings/server/MqttRelayCard";
import { ObicoServerCard } from "@/components/settings/server/ObicoServerCard";
import { PublicUrlCard } from "@/components/settings/server/PublicUrlCard";
import { ServicesCard } from "@/components/settings/server/ServicesCard";
import { SmtpCard } from "@/components/settings/server/SmtpCard";
import { SlicerServerCard } from "@/components/settings/server/SlicerServerCard";
import { OrcaSlicerServerCard } from "@/components/settings/server/OrcaSlicerServerCard";
import { VpNetworkCard } from "@/components/settings/server/VpNetworkCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/** Admin-only Settings -> Server tab body. */
export function ServerTab() {
  const { t } = useTranslation();
  const q = useServerSettings();
  if (q.isLoading) return <Skeleton className="h-72 w-full rounded-xl" />;
  if (q.isError || !q.data) {
    return (
      <div className="space-y-3">
        <p role="alert" className="text-sm text-destructive">
          {q.error instanceof ApiError ? q.error.detail : t("settings.server.loadError")}
        </p>
        <Button type="button" variant="outline" onClick={() => void q.refetch()}>
          {t("settings.retry")}
        </Button>
      </div>
    );
  }
  const data = q.data;
  return (
    <>
      <ServicesCard />
      {data.ignored_env.length ? (
        <div role="alert" className="rounded-md border border-warning/50 bg-warning/10 p-3 text-sm">
          {t("settings.server.ignoredEnv", { names: data.ignored_env.join(", ") })}
        </div>
      ) : null}
      <PublicUrlCard data={data} />
      <LimitsCard data={data} />
      <SlicerServerCard data={data} />
      <OrcaSlicerServerCard data={data} />
      <SmtpCard data={data} />
      <ObicoServerCard />
      <VpNetworkCard data={data} />
      <MqttRelayCard />
      <LogsCard />
    </>
  );
}
