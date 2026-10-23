import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAmsHistory } from "@/api/sensors";
import { HistoryRange } from "@/components/print/modals/HistoryRange";
import { LineChart } from "@/components/stats/LineChart";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { formatHistoryT } from "@/lib/historyFormat";

function UnitChart({ printerId, unit, hours, metric }: { printerId: number; unit: number; hours: number; metric: "humidity" | "temp" }) {
  const { t } = useTranslation();
  const q = useAmsHistory(printerId, unit, hours);
  const pts = (q.data?.points ?? []).map((p) => ({ t: Date.parse(p.ts), v: p[metric] }));
  const title = `${t("history.unit", { n: unit + 1 })} - ${t(`history.${metric}`)}`;
  return (
    <section className="space-y-1" aria-label={title}>
      <h3 className="label-caps">{title}</h3>
      {q.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : q.isError ? (
        <p className="text-sm text-destructive">{t("history.loadError")}</p>
      ) : pts.every((p) => p.v == null) ? (
        <p className="text-sm text-muted-foreground">{t("history.empty")}</p>
      ) : (
        <LineChart
          series={[{ key: metric, label: t(`history.${metric}`), color: metric === "humidity" ? "var(--viz-1)" : "var(--viz-2)", points: pts }]}
          unit={metric === "humidity" ? "%" : "°C"}
          label={title}
          formatT={formatHistoryT(hours)}
        />
      )}
    </section>
  );
}

/** Humidity + temperature history per AMS unit from `/printers/{id}/ams-history`.
 * One chart per metric (never dual-axis). */
export function AmsHistoryModal({
  printerId,
  units = [0],
  open,
  onOpenChange,
}: {
  printerId: number;
  /** AMS unit ids to chart (the card passes the units the printer reports). */
  units?: number[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [hours, setHours] = useState(24);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("history.ams")}</DialogTitle>
          <DialogDescription className="sr-only">{t("history.ams")}</DialogDescription>
        </DialogHeader>
        <HistoryRange hours={hours} onChange={setHours} />
        {open &&
          units.flatMap((u) => [
            <UnitChart key={`${u}h`} printerId={printerId} unit={u} hours={hours} metric="humidity" />,
            <UnitChart key={`${u}t`} printerId={printerId} unit={u} hours={hours} metric="temp" />,
          ])}
      </DialogContent>
    </Dialog>
  );
}
