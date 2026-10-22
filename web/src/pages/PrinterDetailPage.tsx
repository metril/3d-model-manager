import { getRouteApi } from "@tanstack/react-router";

import { usePrinters } from "@/api/printers";
import { PrinterStatusPanel } from "@/components/printer/PrinterStatusPanel";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";

const routeApi = getRouteApi("/authenticated/printers/$id");

/**
 * `/printers/$id` (P3 Task 5 shell): temporarily just the same
 * `PrinterStatusPanel` card `PrinterPage` used to render per-printer, now
 * scoped to one printer by its route param. Task 8 replaces this with the
 * full per-printer detail page.
 */
export function PrinterDetailPage() {
  const { id } = routeApi.useParams();
  const printers = usePrinters();
  const printer = printers.data?.find((p) => String(p.id) === id);

  if (printers.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;

  return (
    <PageContainer width="narrow">
      {printer ? (
        <PrinterStatusPanel printer={printer} />
      ) : (
        <p className="text-sm text-muted-foreground">Printer not found.</p>
      )}
    </PageContainer>
  );
}
