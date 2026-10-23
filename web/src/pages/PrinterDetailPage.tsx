/**
 * `/printers/$id` (P3 Task 10): the per-printer detail page, replacing the
 * old `/printer` page (gone) and the Task 5 shell. Left column is a 16:9
 * camera tile above the restyled `PrinterStatusPanel`; right column is
 * THIS printer's own up-next (`UpNextList`, P6 Task 10: from its
 * `PrinterOverviewItem.up_next`, not the overview's top-level unassigned
 * list) and its own history (`PrintJobHistory printerId`). Mirrors the `/` and
 * `/printer` redirect policies' flag/not-found handling so a stale link or
 * bookmark lands somewhere clear either way.
 *
 * P4 Task 9: the camera slot is a live `CameraTile` only when the viewer
 * has `canPrinter(id, "control")` (the token mint requires it) -- anyone
 * else keeps the plain placeholder icon.
 */
import { getRouteApi } from "@tanstack/react-router";
import { PrinterIcon } from "lucide-react";

import { modelKey, printerImage } from "@/lib/printerState";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import { usePrinters, usePrintersOverview } from "@/api/printers";
import { CameraTile } from "@/components/print/CameraTile";
import { HaAlertBadge } from "@/components/print/HaAlertBadge";
import { UpNextList } from "@/components/print/UpNextList";
import { PrinterStatusPanel } from "@/components/printer/PrinterStatusPanel";
import { PrintJobHistory } from "@/components/printer/PrintJobHistory";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";

const routeApi = getRouteApi("/authenticated/printers/$id");

export function PrinterDetailPage() {
  const { id } = routeApi.useParams();
  const { canPrinter } = useAuth();
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

  return (
    <PageContainer width="default">
      <div className="flex items-center gap-2">
        <img
          src={printerImage(modelKey(printer.model, printer.serial))}
          alt=""
          className="size-12 shrink-0 object-contain"
        />
        <div>
          <h1 className="text-lg font-semibold text-foreground">{printer.name}</h1>
          <p className="text-sm text-muted-foreground">{printer.model ?? printer.kind}</p>
        </div>
        {overviewItem?.ha_alert ? (
          <HaAlertBadge reason={overviewItem.ha_alert} held={overviewItem.ha_held ?? false} />
        ) : null}
      </div>

      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="flex flex-1 flex-col gap-4">
          <div className="aspect-video overflow-hidden rounded-[6px] border bg-muted">
            {canPrinter(printer.id, "print") ? (
              <CameraTile
                printerId={printer.id}
                online={overviewItem?.status.online ?? false}
              />
            ) : (
              <div className="flex size-full items-center justify-center">
                <PrinterIcon className="size-8 text-muted-foreground/50" />
              </div>
            )}
          </div>
          <PrinterStatusPanel printer={printer} />
        </div>

        <div className="flex flex-col gap-6 lg:w-80 lg:shrink-0">
          <UpNextList
            entries={overviewItem?.up_next ?? []}
            printerId={printer.id}
          />
          <PrintJobHistory printerId={printer.id} />
        </div>
      </div>
    </PageContainer>
  );
}
