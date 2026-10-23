import { useState } from "react";
import { useTranslation } from "react-i18next";

import { usePrinterSensors } from "@/api/sensors";
import { HistoryRange } from "@/components/print/modals/HistoryRange";
import { LineChart } from "@/components/stats/LineChart";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { formatHistoryT } from "@/lib/historyFormat";

/** Nozzle / bed / chamber temperature history from `/printers/{id}/sensors`. */
export function HeaterHistoryModal({
  printerId,
  open,
  onOpenChange,
}: {
  printerId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [hours, setHours] = useState(6);
  const q = usePrinterSensors(printerId, hours, open);
  const pts = q.data?.points ?? [];
  const mk = (key: "nozzle" | "bed" | "chamber", color: string) => ({
    key,
    label: t(`history.${key}`),
    color,
    points: pts.map((p) => ({ t: Date.parse(p.ts), v: p[key] })),
  });
  // Chamber is omitted entirely when the printer never reported it.
  const series = [mk("nozzle", "var(--viz-1)"), mk("bed", "var(--viz-2)"), mk("chamber", "var(--viz-3)")].filter((s) =>
    s.points.some((p) => p.v != null),
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("history.heater")}</DialogTitle>
          <DialogDescription className="sr-only">{t("history.heater")}</DialogDescription>
        </DialogHeader>
        <HistoryRange hours={hours} onChange={setHours} />
        {q.isLoading ? (
          <Skeleton className="h-56 w-full" />
        ) : q.isError ? (
          <p className="text-sm text-destructive">{t("history.loadError")}</p>
        ) : series.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("history.empty")}</p>
        ) : (
          <LineChart series={series} unit="°C" label={t("history.heater")} formatT={formatHistoryT(hours)} />
        )}
      </DialogContent>
    </Dialog>
  );
}
