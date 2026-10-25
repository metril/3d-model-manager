/**
 * Print status block: 96 px thumbnail, stage text, print name, progress,
 * remaining / ETA, layer n/total, started-by and the skip-objects button
 * (badge = skippable objects). Idle shows "Last: ..." from the latest run
 * and a plate-clear pill after a finished print.
 */
import { ListXIcon, PrinterIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { usePrinterObjects } from "@/api/printerControl";
import { useClearPlate } from "@/api/printerOps";
import type { ActiveJobOut, ArchiveOut, PrinterStatusOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatEta } from "@/lib/formatEta";
import { toneForGcode } from "@/lib/printerState";

import { etaClock } from "./etaClock";
import { SkipObjectsDialog } from "./SkipObjectsDialog";

function Thumb({ src }: { src: string | null }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="pc-thumb flex shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted">
      {src && !failed ? (
        <img
          src={src}
          alt=""
          className="size-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : (
        <PrinterIcon className="size-6 text-muted-foreground/50" />
      )}
    </div>
  );
}

export function StatusSection({
  printerId,
  status,
  activeJob,
  lastRuns,
  canControl,
}: {
  printerId: number;
  status: PrinterStatusOut;
  activeJob: ActiveJobOut | null;
  lastRuns: ArchiveOut[];
  canControl: boolean;
}) {
  const { t } = useTranslation();
  const { tone } = toneForGcode(status.online, status.gcode_state);
  const active = tone === "printing" || tone === "paused";
  const [skipOpen, setSkipOpen] = useState(false);
  const objects = usePrinterObjects(printerId, active && canControl);
  const skippable = objects.data?.objects ?? [];
  const last = lastRuns[0];
  const clearPlate = useClearPlate(printerId);

  if (!status.online) {
    return (
      <p className="pc-t12 text-muted-foreground" data-slot="status-section">
        {last
          ? t("card.status.offlineLast", { name: last.subtask_name ?? last.model_name ?? t("card.status.print") })
          : t("card.status.offline")}
      </p>
    );
  }

  if (!active) {
    return (
      <div className="flex items-center gap-3" data-slot="status-section">
        <Thumb src={last?.has_thumbnail ? `/api/archives/${last.id}/thumbnail` : null} />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="pc-t12 font-medium">{status.stage && status.stage !== "Idle" ? status.stage : t("card.status.idle")}</p>
          {last ? (
            <p className="pc-t11 truncate text-muted-foreground">
              {t("card.status.last", { name: last.subtask_name ?? last.model_name ?? t("card.status.print"), result: last.result })}
            </p>
          ) : (
            <p className="pc-t11 text-muted-foreground">{t("card.status.nothing")}</p>
          )}
          {status.awaiting_plate_clear ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{t("printerOps.plateNeedsClearing")}</Badge>
              {canControl ? (
                <Button
                  type="button"
                  size="xs"
                  variant="outline"
                  disabled={clearPlate.isPending}
                  onClick={() => void clearPlate.mutateAsync().then(() => toast.success(t("printerOps.plateCleared")), () => undefined)}
                >
                  {t("printerOps.markCleared")}
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  const pct = Math.max(0, Math.min(100, status.mc_percent ?? 0));
  const remaining = formatEta(status.mc_remaining_time);
  const clock = etaClock(status.mc_remaining_time);
  const name = activeJob?.model_name ?? activeJob?.subtask_name ?? status.subtask_name ?? t("card.status.print");
  const thumbArchive = lastRuns.find((r) => activeJob && r.print_job_id === activeJob.id && r.has_thumbnail);
  const thumbSrc = activeJob?.thumbnail_url ?? (thumbArchive ? `/api/archives/${thumbArchive.id}/thumbnail` : null);
  return (
    <div className="flex gap-3" data-slot="status-section">
      <Thumb src={thumbSrc} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <p className="pc-t12 min-w-0 flex-1 truncate font-medium" title={name}>
            {name}
          </p>
          {skippable.length > 1 ? (
            <Button
              type="button"
              variant="outline"
              size="icon-xs"
              className="relative"
              aria-label={t("card.status.skip")}
              onClick={() => setSkipOpen(true)}
            >
              <ListXIcon />
              <span className="absolute -right-1.5 -top-1.5 rounded-full bg-primary px-1 text-[9px] leading-4 text-primary-foreground">
                {skippable.length}
              </span>
            </Button>
          ) : null}
        </div>
        {status.stage ? <p className="pc-t11 text-muted-foreground">{status.stage}</p> : null}
        <div
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full rounded-full bg-state-printing transition-all" style={{ width: `${pct}%` }} />
        </div>
        <div className="pc-t11 flex flex-wrap items-center justify-between gap-x-3 font-mono tabular-nums text-muted-foreground">
          <span>{pct}%</span>
          {remaining ? <span>{t("card.status.left", { t: remaining })}</span> : null}
          {clock ? <span>{t("card.status.eta", { t: clock })}</span> : null}
          <span>
            {t("card.status.layer", { n: status.layer_num ?? 0, total: status.total_layer_num ?? 0 })}
          </span>
        </div>
        {activeJob ? (
          <p className="pc-t10 text-muted-foreground">
            {activeJob.origin === "app" ? t("card.status.startedApp") : t("card.status.startedPrinter")}
          </p>
        ) : null}
      </div>
      {skippable.length > 1 ? (
        <SkipObjectsDialog printerId={printerId} objects={skippable} open={skipOpen} onOpenChange={setSkipOpen} />
      ) : null}
    </div>
  );
}
