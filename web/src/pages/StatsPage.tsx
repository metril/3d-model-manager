import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { useStatsDashboard } from "@/api/stats";
import { StatsGrid } from "@/components/stats/StatsGrid";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DAY_OPTIONS,
  DEFAULT_PREFS,
  cycleSize,
  loadPrefs,
  reorder,
  savePrefs,
  type StatsPrefs,
} from "@/lib/statsLayout";

export function StatsPage() {
  const { t } = useTranslation();
  const { data: me } = useAuth();
  const userId = me?.id;
  const [prefs, setPrefs] = useState<StatsPrefs>(() => loadPrefs(userId));
  useEffect(() => setPrefs(loadPrefs(userId)), [userId]);

  function update(next: StatsPrefs) {
    setPrefs(next);
    savePrefs(userId, next);
  }
  const stats = useStatsDashboard(prefs.days);

  return (
    <PageContainer width="default">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("stats.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("stats.subtitle")}</p>
        </div>
        <div role="group" aria-label={t("stats.range")} className="flex gap-1">
          {DAY_OPTIONS.map((d) => (
            <Button
              key={d}
              type="button"
              size="sm"
              variant={prefs.days === d ? "default" : "outline"}
              aria-pressed={prefs.days === d}
              onClick={() => update({ ...prefs, days: d })}
            >
              {t("stats.days", { count: d })}
            </Button>
          ))}
        </div>
        <Button type="button" size="sm" variant="ghost" onClick={() => update({ ...DEFAULT_PREFS, days: prefs.days })}>
          {t("stats.reset")}
        </Button>
      </div>
      {stats.isLoading ? (
        <Skeleton className="h-72 w-full rounded-xl" />
      ) : stats.isError || !stats.data ? (
        <Card className="mx-auto mt-12 max-w-md">
          <CardHeader className="text-center">
            <CardTitle>{t("stats.loadError")}</CardTitle>
            <CardDescription>
              <Button type="button" size="sm" onClick={() => void stats.refetch()}>
                {t("settings.retry")}
              </Button>
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <StatsGrid
          widgets={prefs.widgets}
          data={stats.data}
          onReorder={(a, o) => update({ ...prefs, widgets: reorder(prefs.widgets, a, o) })}
          onResize={(id) => update({ ...prefs, widgets: cycleSize(prefs.widgets, id) })}
        />
      )}
    </PageContainer>
  );
}
