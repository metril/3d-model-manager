import { useTranslation } from "react-i18next";

import { useAdminStatus, type ServiceState } from "@/api/adminStatus";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const stateDotClass: Record<ServiceState, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  down: "bg-destructive",
  off: "bg-muted-foreground/40",
};

/** Settings -> Server: live health of every service (GET /admin/status). */
export function ServicesCard() {
  const { t } = useTranslation();
  const q = useAdminStatus();
  if (q.isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;
  const data = q.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {data ? (
            <span
              role="img"
              aria-label={t(`settings.server.services.overall.${data.overall}`)}
              className={cn("size-2.5 rounded-full", stateDotClass[data.overall])}
            />
          ) : null}
          {t("settings.server.services.title")}
        </CardTitle>
        <CardDescription>{t("settings.server.services.desc")}</CardDescription>
      </CardHeader>
      <CardContent>
        {!data ? (
          <p role="alert" className="text-sm text-destructive">
            {t("settings.server.services.loadError")}
          </p>
        ) : (
          <ul className="divide-y">
            {Object.entries(data.services).map(([id, s]) => (
              <li key={id} data-testid={`svc-${id}`} className="flex items-center gap-3 py-2 text-sm">
                <span
                  role="img"
                  aria-label={t(`settings.server.services.state.${s.state}`)}
                  className={cn("size-2.5 shrink-0 rounded-full", stateDotClass[s.state])}
                />
                <span className="w-44 shrink-0 font-medium">{t(`settings.server.services.names.${id}`, { defaultValue: id })}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground">{s.detail}</span>
                {s.version ? <span className="shrink-0 font-mono text-xs text-muted-foreground">{s.version.slice(0, 12)}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
