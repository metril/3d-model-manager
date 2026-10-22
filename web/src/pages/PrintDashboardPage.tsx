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
 */
import type { ReactNode } from "react";

import { usePrintersOverview } from "@/api/printers";
import { PrinterCard } from "@/components/print/PrinterCard";
import { RecentRunsList } from "@/components/print/RecentRunsList";
import { UpNextList } from "@/components/print/UpNextList";
import { PrinterSetupCard } from "@/components/settings/PrinterSetupCard";
import { PageContainer } from "@/components/ui/page-container";

function gridClassName(count: number): string {
  if (count >= 3) return "grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3";
  if (count === 2) return "grid grid-cols-1 gap-4 md:grid-cols-2";
  return "grid grid-cols-1 gap-4";
}

export function PrintDashboardPage() {
  const overview = usePrintersOverview();
  const printers = overview.data?.printers ?? [];
  const upNext = overview.data?.up_next ?? [];
  const recentRuns = overview.data?.recent_runs ?? [];
  // P3 renders no rail extras; a later milestone (energy/cost tiles, etc.)
  // slots in here without PrintDashboardPage itself changing shape.
  const railExtras: ReactNode[] = [];

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
            <PrinterCard key={item.printer.id} item={item} />
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
