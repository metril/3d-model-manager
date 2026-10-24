/** Upcoming/running drying schedules, across printers or for one printer (compact, inside the dialog). */
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { useAllDryingSchedules, useCancelSchedule, useDryingSchedules } from "@/api/drying";
import { usePrinters } from "@/api/printers";
import type { ScheduledDrying } from "@/api/types";
import { unitLabel } from "@/components/print/card/ams";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const LIVE = new Set(["pending", "waiting", "running"]);

function Row({ s, printerName, showPrinter }: { s: ScheduledDrying; printerName: string; showPrinter: boolean }) {
  const { t } = useTranslation();
  const { canPrinter } = useAuth();
  const cancel = useCancelSchedule();
  const when = s.start_after ? new Date(s.start_after).toLocaleString() : t("drying.asap");
  const reason = s.status === "waiting" && s.waiting_reason ? t(`drying.waiting.${s.waiting_reason}`, { defaultValue: s.waiting_reason }) : null;
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm" data-testid="dry-schedule">
      {showPrinter ? <span className="font-medium">{printerName}</span> : null}
      <span>{unitLabel(s.ams_id)}</span>
      <span>
        {s.filament} · {s.temp}°C · {s.duration_hours}h
      </span>
      <span className="text-muted-foreground">{when}</span>
      <Badge variant="secondary">{t(`drying.state.${s.status}`)}</Badge>
      {s.source !== "manual" ? <Badge variant="outline">{t(`drying.list.source.${s.source}`)}</Badge> : null}
      {reason ? <span className="text-xs text-muted-foreground">{reason}</span> : null}
      {s.status !== "running" && canPrinter(s.printer_id, "control") ? (
        <Button type="button" size="xs" variant="ghost" className="ml-auto" disabled={cancel.isPending} onClick={() => cancel.mutate(s)}>
          {t("drying.cancel")}
        </Button>
      ) : null}
    </li>
  );
}

export function ScheduledDryingsList({ printerId, amsId, compact }: { printerId?: number; amsId?: number; compact?: boolean }) {
  const { t } = useTranslation();
  const printers = usePrinters();
  const one = useDryingSchedules(printerId ?? 0, printerId != null);
  const all = useAllDryingSchedules(printerId != null ? [] : (printers.data ?? []).map((p) => p.id));
  const rows = (printerId != null ? (one.data ?? []) : all.data)
    .filter((s) => LIVE.has(s.status) && (amsId == null || s.ams_id === amsId))
    .sort((a, b) => (a.start_after ?? "").localeCompare(b.start_after ?? ""));
  const name = (id: number) => printers.data?.find((p) => p.id === id)?.name ?? `#${id}`;
  const body =
    rows.length === 0 ? (
      <p className="text-sm text-muted-foreground">{t("drying.noSchedules")}</p>
    ) : (
      <ul className="space-y-1.5">
        {rows.map((s) => (
          <Row key={s.id} s={s} printerName={name(s.printer_id)} showPrinter={printerId == null} />
        ))}
      </ul>
    );
  if (compact) return body;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("drying.list.title")}</CardTitle>
        <CardDescription>{t("drying.list.description")}</CardDescription>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
