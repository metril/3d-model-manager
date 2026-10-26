/**
 * `/printers/$id` (Q3): the XL printer card (with a large live `CameraTile`
 * in its camera slot) beside the printer's own up-next and run history
 * (`UpNextList`, `PrintJobHistory`), then the print/library `HistoryStats`.
 * Mirrors the `/` and `/printer` redirect policies' flag/not-found handling
 * so a stale link or bookmark lands somewhere clear either way.
 *
 * The camera tile is live only when the viewer has `canPrinter(id, "print")`
 * (the token mint requires it) -- anyone else keeps the plain placeholder.
 * Q12: badges, footer items and info/HMS/diagnose modals come from the shared
 * `usePrinterCardExtras` (via `ManagedPrinterCard`), same as the dashboard.
 */
import { getRouteApi } from "@tanstack/react-router";
import { PrinterIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import { usePrinters, usePrintersOverview } from "@/api/printers";
import type { PrinterOut, PrintersOverviewOut } from "@/api/types";
import { MaintenanceSection } from "@/components/maintenance/MaintenanceSection";
import { CameraTile } from "@/components/print/CameraTile";
import { HaAlertBadge } from "@/components/print/HaAlertBadge";
import { PlateStallBadge } from "@/components/print/PlateStallBadge";
import { HistoryStats } from "@/components/print/HistoryStats";
import { ManagedPrinterCard } from "@/components/print/ManagedPrinterCard";
import { UpNextList } from "@/components/print/UpNextList";
import { PrintJobHistory } from "@/components/printer/PrintJobHistory";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";

const routeApi = getRouteApi("/authenticated/printers/$id");

export function PrinterDetailPage() {
  const { id } = routeApi.useParams();
  const features = useFeatures();
  const enabled = !!features.data?.printer_enabled;
  const printers = usePrinters({ enabled });
  const overview = usePrintersOverview({ enabled });

  if (features.isLoading) return <Skeleton className="h-64 w-full rounded-xl" />;

  if (!enabled)
    return (
      <Card className="mx-auto mt-12 max-w-md">
        <CardHeader className="text-center">
          <CardTitle>Printer integration is off</CardTitle>
          <CardDescription>
            Turn it on under Settings → Printer to connect your Bambu printer over the LAN. It
            takes effect immediately — no restart.
          </CardDescription>
        </CardHeader>
      </Card>
    );

  if (printers.isLoading) return <Skeleton className="h-64 w-full rounded-xl" />;

  const printer = printers.data?.find((p) => String(p.id) === id);

  if (!printer)
    return (
      <Card className="mx-auto mt-12 max-w-md">
        <CardHeader className="text-center">
          <CardTitle>Printer not found</CardTitle>
          <CardDescription>It may have been removed, or you don&apos;t have access to it.</CardDescription>
        </CardHeader>
      </Card>
    );

  const overviewItem = overview.data?.printers.find((p) => p.printer.id === printer.id);
  if (!overviewItem) return <Skeleton className="h-64 w-full rounded-xl" />;

  return <PrinterDetailBody printer={printer} overviewItem={overviewItem} />;
}

function PrinterDetailBody({
  printer,
  overviewItem,
}: {
  printer: PrinterOut;
  overviewItem: PrintersOverviewOut["printers"][number];
}) {
  const { canPrinter } = useAuth();

  return (
    <PageContainer width="fluid">
      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-4 lg:max-w-4xl">
          <ManagedPrinterCard
            item={{
              ...overviewItem,
              can_print: overviewItem.can_print && canPrinter(printer.id, "print"),
              can_control: overviewItem.can_control && canPrinter(printer.id, "control"),
            }}
            size="XL"
            camera={
              canPrinter(printer.id, "print") ? (
                <CameraTile printerId={printer.id} online={overviewItem.status.online} />
              ) : (
                <div className="flex size-full items-center justify-center">
                  <PrinterIcon className="size-8 text-muted-foreground/50" />
                </div>
              )
            }
            badge={
              overviewItem.ha_alert || overviewItem.plate_check_stalled ? (
                <>
                  {overviewItem.ha_alert ? (
                    <HaAlertBadge reason={overviewItem.ha_alert} held={overviewItem.ha_held ?? false} />
                  ) : null}
                  {overviewItem.plate_check_stalled ? <PlateStallBadge /> : null}
                </>
              ) : undefined
            }
          />
          <MaintenanceSection
            printerId={printer.id}
            maintenanceMode={printer.maintenance_mode ?? false}
            canControl={canPrinter(printer.id, "control")}
          />
        </div>

        <div className="flex flex-col gap-6 lg:w-96 lg:shrink-0">
          <UpNextList entries={overviewItem.up_next} printerId={printer.id} />
          <PrintJobHistory printerId={printer.id} />
        </div>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-foreground">Stats</h2>
        <HistoryStats />
      </section>
    </PageContainer>
  );
}
