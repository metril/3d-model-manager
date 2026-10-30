/**
 * Job block: 64 px thumbnail, print name, big percent, state-coloured progress
 * bar (striped while paused) and the ETA row. Stage text shows only when it adds
 * something beyond the header's state pill. Idle shows "Last: ..." from the
 * latest run plus the plate-clear pill; failed freezes the bar in red; offline
 * shows the last print only.
 */
import { PrinterIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { useClearPlate } from "@/api/printerOps";
import type { ActiveJobOut, ArchiveOut, PrinterStatusOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SharedPlugPowerOn } from "@/components/print/PlugRow";
import { formatEta } from "@/lib/formatEta";
import { formatRelativeTime } from "@/lib/format";
import { toneForGcode, type StateTone } from "@/lib/printerState";
import { cn } from "@/lib/utils";

import { etaClock } from "./etaClock";
import { fillStyle, trackStyle } from "./tone";

function Thumb({ src, small = false, dim = false }: { src: string | null; small?: boolean; dim?: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted",
        small ? "size-12" : "size-16",
        dim && "grayscale",
      )}
    >
      {src && !failed ? (
        <img src={src} alt="" className="size-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <PrinterIcon className="size-6 text-muted-foreground/50" />
      )}
    </div>
  );
}

/** Progress bar: `h-2` track at 16% of the state colour, fill in the state colour. */
export function ProgressBar({ tone, pct, className }: { tone: StateTone; pct: number; className?: string }) {
  const { t } = useTranslation();
  return (
    <div
      role="progressbar"
      aria-label={t("card.status.progress")}
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn("h-2 w-full overflow-hidden rounded-full", className)}
      style={trackStyle(tone)}
    >
      <div className="h-full rounded-full transition-all" style={fillStyle(tone, pct)} />
    </div>
  );
}

const GENERIC_STAGES = new Set(["idle", "printing", "paused", "offline", "finished", "failed"]);

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
  const { tone, label } = toneForGcode(status.online, status.gcode_state);
  const active = tone === "printing" || tone === "paused";
  const last = lastRuns[0];
  const clearPlate = useClearPlate(printerId);

  if (!status.online) {
    const seen = status.last_report_at ? formatRelativeTime(status.last_report_at) : null;
    return (
      <div className="flex flex-col items-start gap-1.5 empty:hidden" data-slot="status-section">
        {seen && seen !== "—" ? <p className="pc-t12 text-muted-foreground">{t("card.status.offlineSeen", { when: seen })}</p> : null}
        {last ? (
          <p className="pc-t12 max-w-full truncate text-muted-foreground">
            {t("card.status.lastPrint", { name: last.subtask_name ?? last.model_name ?? t("card.status.print") })}
          </p>
        ) : null}
        <SharedPlugPowerOn printerId={printerId} canControl={canControl} label={t("card.status.powerOn")} />
      </div>
    );
  }

  if (!active) {
    const failedPct = tone === "failed" && status.mc_percent != null ? Math.max(0, Math.min(100, status.mc_percent)) : null;
    return (
      <div className="flex items-center gap-3" data-slot="status-section">
        <Thumb small src={last?.has_thumbnail ? `/api/archives/${last.id}/thumbnail` : null} />
        <div className="min-w-0 flex-1 space-y-1">
          {last ? (
            <p className="pc-t12 truncate text-muted-foreground">
              {t("card.status.last", { name: last.subtask_name ?? last.model_name ?? t("card.status.print"), result: last.result })}
            </p>
          ) : (
            <p className="pc-t12 text-muted-foreground">{t("card.status.nothing")}</p>
          )}
          {failedPct != null ? <ProgressBar tone="failed" pct={failedPct} /> : null}
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
  const stage = status.stage && !GENERIC_STAGES.has(status.stage.toLowerCase()) && status.stage !== label ? status.stage : null;
  return (
    <div className="flex flex-col gap-2" data-slot="status-section">
      <div className="flex items-center gap-3">
        <Thumb src={thumbSrc} />
        <div className="min-w-0 flex-1">
          <p className="pc-t12 truncate font-medium" title={name}>
            {name}
          </p>
          {stage ? <p className="pc-t11 truncate text-muted-foreground">{stage}</p> : null}
          {activeJob ? (
            <p className="pc-t10 truncate text-muted-foreground">
              {activeJob.origin === "app" ? t("card.status.startedApp") : t("card.status.startedPrinter")}
            </p>
          ) : null}
        </div>
        <span className="font-mono text-2xl font-semibold tabular-nums leading-none" data-testid="job-percent">
          {pct}
          <span className="text-base font-normal text-muted-foreground">%</span>
        </span>
      </div>
      <ProgressBar tone={tone} pct={pct} />
      <div className="pc-t11 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono tabular-nums text-muted-foreground">
        {clock ? <span className="font-semibold text-foreground">{t("card.status.eta", { t: clock })}</span> : null}
        {remaining ? <span>{t("card.status.left", { t: remaining })}</span> : null}
        <span className="ml-auto">{t("card.status.layer", { n: status.layer_num ?? 0, total: status.total_layer_num ?? 0 })}</span>
      </div>
      {status.gcode_state === "RUNNING" && status.next_pause ? (
        <Badge variant="secondary" className="self-start" data-testid="next-pause">
          {status.next_pause.eta_s != null
            ? t("card.status.nextPause", { t: formatEta(status.next_pause.eta_s / 60), n: status.next_pause.layer })
            : t("card.status.nextPauseLayer", { n: status.next_pause.layer })}
        </Badge>
      ) : null}
    </div>
  );
}
