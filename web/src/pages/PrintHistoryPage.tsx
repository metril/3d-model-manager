/**
 * `/print/history` (P3 Task 9): runs + stats tabs, synced to `?tab=` so the
 * URL stays a durable bookmark/back-button target (same contract the Task
 * 5 shell set up). `tab=stats` renders `HistoryStats` (the moved
 * `DashboardPage`); the default `tab=runs` renders `PrintJobHistory`
 * unscoped. With the printer feature off there is nothing to show on the
 * runs tab, so the tab list itself is hidden and only stats render --
 * matches the `/` and `/printer` redirect policies' treatment of the flag.
 */
import { getRouteApi } from "@tanstack/react-router";

import { useFeatures } from "@/api/features";
import { HistoryStats } from "@/components/print/HistoryStats";
import { PrintJobHistory } from "@/components/printer/PrintJobHistory";
import { PageContainer } from "@/components/ui/page-container";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const routeApi = getRouteApi("/authenticated/print/history");

export function PrintHistoryPage() {
  const { tab } = routeApi.useSearch();
  const navigate = routeApi.useNavigate();
  const features = useFeatures();
  const printerEnabled = !!features.data?.printer_enabled;
  // Stats-only while the flag is off (or still loading) -- `HistoryStats`
  // stays mounted in the SAME `Tabs` subtree either way, rather than
  // swapping between two JSX shapes, so a `features` resolve mid-render
  // doesn't tear down and remount it.
  const activeTab = printerEnabled ? tab : "stats";

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-lg font-semibold text-foreground">History</h1>
        <p className="text-sm text-muted-foreground">
          {printerEnabled ? "Print runs and library stats." : "Library stats."}
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={(v) => navigate({ search: { tab: v as "runs" | "stats" } })}>
        {printerEnabled ? (
          <TabsList>
            <TabsTrigger value="runs">Runs</TabsTrigger>
            <TabsTrigger value="stats">Stats</TabsTrigger>
          </TabsList>
        ) : null}
        <TabsContent value="runs">
          <PrintJobHistory />
        </TabsContent>
        <TabsContent value="stats">
          <HistoryStats />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
