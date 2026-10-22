import { getRouteApi } from "@tanstack/react-router";

import { PrintJobHistory } from "@/components/printer/PrintJobHistory";
import { PageContainer } from "@/components/ui/page-container";
import { DashboardPage } from "@/pages/DashboardPage";

const routeApi = getRouteApi("/authenticated/print/history");

/**
 * `/print/history` (P3 Task 5 shell): a temporary merge of the old
 * `/dashboard` (library stats, now `tab=stats`) and `/jobs`-adjacent print
 * run history (`tab=runs`, the default) behind one route so `/dashboard`
 * bookmarks land somewhere real. Task 9/10 replace this with an actual
 * tabbed page; until then it just renders whichever existing component the
 * `tab` search param picks.
 */
export function PrintHistoryPage() {
  const { tab } = routeApi.useSearch();
  if (tab === "stats") return <DashboardPage />;
  return (
    <PageContainer width="narrow">
      <PrintJobHistory />
    </PageContainer>
  );
}
