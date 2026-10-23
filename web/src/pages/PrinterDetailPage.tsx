/**
 * `/printers/$id` (Q3): the XL printer card (with a large live `CameraTile`
 * in its camera slot) beside the printer's own up-next and run history
 * (`UpNextList`, `PrintJobHistory`), then the print/library `HistoryStats`.
 * Mirrors the `/` and `/printer` redirect policies' flag/not-found handling
 * so a stale link or bookmark lands somewhere clear either way.
 *
 * The camera tile is live only when the viewer has `canPrinter(id, "print")`
 * (the token mint requires it) -- anyone else keeps the plain placeholder.
 * Q5: info/HMS/diagnose open via the card's callbacks (`usePrinterModals`);
 * Firmware is a footer button. TODO(Q7-web): `MaintenanceBadge`/`PlugRow`/
 * `MaintenanceSection`; TODO(Q4-web): `CameraSplitButton` via `footerExtra`.
 */
import { getRouteApi } from "@tanstack/react-router";
import { PrinterIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import { usePrinters, usePrintersOverview } from "@/api/printers";
import type { PrinterOut, PrintersOverviewOut } from "@/api/types";
import { usePrinterModals } from "@/components/print/modals";
import { Button } from "@/components/ui/button";
import { CameraTile } from "@/components/print/CameraTile";
import { HaAlertBadge } from "@/components/print/HaAlertBadge";
import { HistoryStats } from "@/components/print/HistoryStats";
import { AmsHistoryModal } from "@/components/print/modals/AmsHistoryModal";
import { HeaterHistoryModal } from "@/components/print/modals/HeaterHistoryModal";
import { PrinterCard } from "@/components/print/PrinterCard";
import { UpNextList } from "@/components/print/UpNextList";
import { PrintJobHistory } from "@/components/printer/PrintJobHistory";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";

const routeApi = getRouteApi("/authenticated/printers/$id");

/** Q8c history buttons (card `footerExtra`): temperature always, AMS only
 * when the printer reports units (`status.ams_units[].id`). */
function HistoryButtons({ printerId, amsUnits }: { printerId: number; amsUnits: number[] }) {
  const { t } = useTranslation();
  const [modal, setModal] = useState<"heater" | "ams" | null>(null);
  return (
    <div className="flex gap-2">
      <Button type="button" size="sm" variant="outline" onClick={() => setModal("heater")}>
        {t("history.heaterBtn")}
      </Button>
      {amsUnits.length > 0 && (
        <Button type="button" size="sm" variant="outline" onClick={() => setModal("ams")}>
          {t("history.amsBtn")}
        </Button>
      )}
      <HeaterHistoryModal printerId={printerId} open={modal === "heater"} onOpenChange={(o) => !o && setModal(null)} />
      <AmsHistoryModal printerId={printerId} units={amsUnits} open={modal === "ams"} onOpenChange={(o) => !o && setModal(null)} />
    </div>
  );
}

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
  const { can, canPrinter } = useAuth();
  const modals = usePrinterModals(printer.id);

  return (
    <PageContainer width="fluid">
      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="min-w-0 flex-1 lg:max-w-4xl">
          <PrinterCard
            item={{
              ...overviewItem,
              can_print: overviewItem.can_print && canPrinter(printer.id, "print"),
              can_control: overviewItem.can_control && canPrinter(printer.id, "control"),
            }}
            size="XL"
            onInfo={() => modals.open("info")}
            onDiagnose={can("settings:write") ? () => modals.open("diagnostic") : undefined}
            onHms={canPrinter(printer.id, "print") ? () => modals.open("hms") : undefined}
            footerExtra={
              <Button variant="outline" size="sm" onClick={() => modals.open("firmware")}>
                Firmware
              </Button>
            }
            camera={
              canPrinter(printer.id, "print") ? (
                <CameraTile printerId={printer.id} online={overviewItem.status.online} />
              ) : (
                <div className="flex size-full items-center justify-center">
                  <PrinterIcon className="size-8 text-muted-foreground/50" />
                </div>
              )
            }
            footerExtra={
              canPrinter(printer.id, "print") ? (
                <HistoryButtons
                  printerId={printer.id}
                  amsUnits={overviewItem.status.ams_units?.map((u) => u.id) ?? []}
                />
              ) : undefined
            }
            badge={
              overviewItem.ha_alert ? (
                <HaAlertBadge reason={overviewItem.ha_alert} held={overviewItem.ha_held ?? false} />
              ) : undefined
            }
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
      {modals.modals}
    </PageContainer>
  );
}
