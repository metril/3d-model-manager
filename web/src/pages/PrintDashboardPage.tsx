/**
 * `/` print-first home (P3 Task 8): a `PrinterCard` grid plus a right rail
 * (`UpNextList`, `RecentRunsList`) fed by one `usePrintersOverview` poll.
 * `redirectForIndex` (`routes.tsx`) already keeps anyone without a visible
 * printer off this route unless they're an admin who could add one, so a
 * zero-printer render here shows the setup card rather than an empty state.
 *
 * Grid density: 1 printer stays single-column (a 2-up grid for one card
 * looks broken), 2 goes `md:grid-cols-2`, 3+ goes `lg:grid-cols-3` --
 * mirrors the gallery's own breakpoint conventions.
 *
 * P4 Task 9: each card's `camera` slot gets a live `CameraTile` for
 * `can_print` items only -- the token mint requires that permission, so
 * a viewer without it keeps the plain placeholder instead of a tile that
 * would just sit offline.
 */
import type { ReactNode } from "react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { usePrintersOverview } from "@/api/printers";
import { useReviewCount } from "@/api/queue";
import { CameraTile } from "@/components/print/CameraTile";
import { HaAlertBadge } from "@/components/print/HaAlertBadge";
import { PrinterCard } from "@/components/print/PrinterCard";
import { RecentRunsList } from "@/components/print/RecentRunsList";
import { ReviewInboxCard } from "@/components/print/ReviewInboxCard";
import { UpNextList } from "@/components/print/UpNextList";
import { PrinterSetupCard } from "@/components/settings/PrinterSetupCard";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-container";

function gridClassName(count: number): string {
  if (count >= 3) return "grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3";
  if (count === 2) return "grid grid-cols-1 gap-4 md:grid-cols-2";
  return "grid grid-cols-1 gap-4";
}

export function PrintDashboardPage() {
  const overview = usePrintersOverview();
  const auth = useAuth();
  const canReview = auth.can("queue:review");
  const reviewCount = useReviewCount({ enabled: canReview });
  const printers = overview.data?.printers ?? [];
  const upNext = overview.data?.up_next ?? [];
  const recentRuns = overview.data?.recent_runs ?? [];
  // P7 Task 9: the review inbox card joins the rail only for a reviewer
  // with something actually pending -- empty-but-mounted would just be
  // dead chrome.
  const railExtras: ReactNode[] = [];
  if (canReview && (reviewCount.data?.count ?? 0) > 0) {
    railExtras.push(<ReviewInboxCard key="review-inbox" />);
  }

  if (overview.isError && !overview.data) {
    return (
      <PageContainer width="default">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Print</h1>
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
          <h1 className="text-lg font-semibold text-foreground">Print</h1>
          <p className="text-sm text-muted-foreground">Add a printer to start printing from here.</p>
        </div>
        <PrinterSetupCard />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="fluid">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Print</h1>
        <p className="text-sm text-muted-foreground">A live overview of your printers.</p>
      </div>

      <div className="flex flex-col gap-6 xl:flex-row">
        <div className={`flex-1 ${gridClassName(printers.length)}`}>
          {printers.map((item) => (
            <PrinterCard
              key={item.printer.id}
              item={item}
              camera={
                item.can_print ? (
                  <CameraTile printerId={item.printer.id} online={item.status.online} />
                ) : undefined
              }
              badge={item.ha_alert ? <HaAlertBadge reason={item.ha_alert} held={item.ha_held ?? false} /> : undefined}
            />
          ))}
        </div>

        <div className="flex flex-col gap-6 xl:w-80 xl:shrink-0">
          <UpNextList entries={upNext} />
          <RecentRunsList runs={recentRuns} />
          {railExtras}
        </div>
      </div>
    </PageContainer>
  );
}
