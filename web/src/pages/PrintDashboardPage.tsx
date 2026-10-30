/**
 * `/` print-first home (P3 Task 8, Q3): a status summary bar, filter/sort
 * toolbar (URL params, `printerDashSearch.ts`), bulk-select toolbar and a
 * grid of bambuddy-layout `PrinterCard`s plus a right rail (`UpNextList`,
 * `RecentRunsList`), all fed by one `usePrintersOverview` poll.
 * `redirectForIndex` (`routes.tsx`) already keeps anyone without a visible
 * printer off this route unless they're an admin who could add one, so a
 * zero-printer render here shows the setup card rather than an empty state.
 *
 * Card size (S/M/L/XL) lives in localStorage (`useCardSize`) and drives the
 * grid min-width (`auto-fill minmax`; default L for 2 printers or fewer). A live `CameraTile` is only mounted on L/XL cards
 * for `can_print` items (the token mint requires that permission; smaller
 * cards link to the detail page's camera instead of streaming N feeds).
 */
import { useNavigate, useSearch } from "@tanstack/react-router";
import type { DockviewApi } from "dockview-react";
import { useMemo, useState, type ReactNode } from "react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { usePrintersOverview } from "@/api/printers";
import { useReviewCount } from "@/api/queue";
import { useCardSize } from "@/components/print/card/cardSize";
import { PrintDock } from "@/components/print/dock/PrintDock";
import { PanelsMenu } from "@/components/print/dock/PanelsMenu";
import { PrintDashProvider, type PrintDashValue } from "@/components/print/dock/PrintDashContext";
import { PrintersPanel } from "@/components/print/dock/PrintersPanel";
import { RailStack } from "@/components/print/dock/RailPanels";
import { readDockMode, writeDockMode } from "@/components/print/dock/layoutStore";
import { ReviewInboxCard } from "@/components/print/ReviewInboxCard";
import { PrinterSetupCard } from "@/components/settings/PrinterSetupCard";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-container";
import { parsePrintDashSearch, type PrintDashSearch } from "@/pages/printerDashSearch";
import { usePrintTitle } from "@/hooks/useDocumentTitle";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { filterItems, sortGroups, summarize } from "@/lib/printerDashboard";

export function PrintDashboardPage() {
  const overview = usePrintersOverview();
  const navigate = useNavigate();
  const search = parsePrintDashSearch(useSearch({ strict: false }) as Record<string, unknown>);
  // >=900px: the customisable dock is available (opt-in; the classic page is the default).
  const canDock = useMediaQuery("(min-width: 900px)");
  const [dockPref, setDockPref] = useState(readDockMode);
  const [dockApi, setDockApi] = useState<DockviewApi | null>(null);
  const dockOn = canDock && dockPref;
  function toggleDock() {
    writeDockMode(!dockPref);
    setDockPref(!dockPref);
    setDockApi(null);
  }
  function patchSearch(patch: Partial<PrintDashSearch>) {
    void navigate({
      to: ".",
      search: ((prev: Record<string, unknown>) => ({ ...prev, ...patch })) as never,
      replace: true,
    });
  }
  const auth = useAuth();
  const canReview = auth.can("queue:review");
  const reviewCount = useReviewCount({ enabled: canReview });
  const printers = useMemo(() => overview.data?.printers ?? [], [overview.data]);
  const [size, setSize] = useCardSize(printers.length > 0 && printers.length <= 2 ? "L" : "M");
  usePrintTitle(printers);
  const summary = useMemo(() => summarize(printers), [printers]);
  const groups = useMemo(() => sortGroups(filterItems(printers, search), search.sort), [printers, search]);
  const upNext = overview.data?.up_next ?? [];
  const recentRuns = overview.data?.recent_runs ?? [];
  // P7 Task 9: the review inbox card joins the rail only for a reviewer
  // with something actually pending -- empty-but-mounted would just be
  // dead chrome.
  const railExtras: ReactNode[] = [];
  if (canReview && (reviewCount.data?.count ?? 0) > 0) {
    railExtras.push(<ReviewInboxCard key="review-inbox" />);
  }

  const dash: PrintDashValue = {
    printers,
    groups,
    summary,
    search,
    patchSearch,
    size,
    setSize,
    upNext,
    recentRuns,
    railExtras,
    refetch: () => void overview.refetch(),
  };

  if (overview.isError && !overview.data) {
    return (
      <PageContainer width="default">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Print</h1>
        </div>
        <div className="space-y-2 py-8 text-center">
          <p role="alert" className="text-sm text-destructive">
            {overview.error instanceof ApiError ? overview.error.detail : "Couldn't load the print overview."}
          </p>
          <Button type="button" variant="outline" onClick={() => void overview.refetch()}>
            Retry
          </Button>
        </div>
      </PageContainer>
    );
  }

  if (!overview.isLoading && printers.length === 0) {
    return (
      <PageContainer width="default">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Print</h1>
          <p className="text-sm text-muted-foreground">Add a printer to start printing from here.</p>
        </div>
        <PrinterSetupCard />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="fluid">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Print</h1>
          <p className="text-sm text-muted-foreground">A live overview of your printers.</p>
        </div>
        {canDock ? (
          <div className="flex items-center gap-2">
            {dockOn && dockApi ? <PanelsMenu api={dockApi} /> : null}
            <Button type="button" size="sm" variant="outline" aria-pressed={dockOn} onClick={toggleDock}>
              {dockOn ? "Classic layout" : "Customize layout"}
            </Button>
          </div>
        ) : null}
      </div>

      <PrintDashProvider value={dash}>
        {dockOn ? (
          <PrintDock onApi={setDockApi} />
        ) : (
          <div className="flex flex-col gap-6 xl:flex-row">
            <PrintersPanel />
            <div className="xl:w-80 xl:shrink-0">
              <RailStack />
            </div>
          </div>
        )}
      </PrintDashProvider>
    </PageContainer>
  );
}
