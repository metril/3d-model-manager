/**
 * Obico failure-detection badge (Q10): smoothed score while watching,
 * "detection paused" after repeated ML errors, "detection error" while the
 * last ML call is failing, and "detection idle" when enabled but no print is
 * running. Renders nothing when detection is off for the printer. Exported for the Q3 card's BadgeRow.
 */
import { useDetectionSummary, usePrinterDetection } from "@/api/detection";
import type { DetectionStatus } from "@/api/detection";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

const pill = "rounded-full border px-2 py-0.5 text-xs";

export function DetectionBadgeView({ status }: { status: DetectionStatus }) {
  const { t } = useTranslation();
  const phase =
    status.phase ??
    (status.detection_paused ? "paused" : status.active && status.smoothed != null ? "watching" : "idle");
  if (phase === "paused") {
    return (
      <span className={cn(pill, "border-warning/40 bg-warning/10 text-warning")}>
        {t("detection.paused")}
      </span>
    );
  }
  if (phase === "error") {
    return (
      <span
        className={cn(pill, "border-destructive/40 bg-destructive/10 text-destructive")}
        title={status.last_error ? t("detection.lastError", { error: status.last_error }) : undefined}
      >
        {t("detection.error")}
      </span>
    );
  }
  if (phase === "idle") {
    if (!status.enabled) return null;
    return (
      <span className={cn(pill, "border-border text-muted-foreground")} title={t("detection.waiting")}>
        {t("detection.idle")}
      </span>
    );
  }
  if (status.smoothed == null) {
    return (
      <span className={cn(pill, "border-border text-muted-foreground")} title={t("detection.warming")}>
        {t("detection.watching")}
      </span>
    );
  }
  const tone =
    status.state === "pause"
      ? "border-destructive/40 bg-destructive/10 text-destructive"
      : status.state === "warn"
        ? "border-warning/40 bg-warning/10 text-warning"
        : "border-border text-muted-foreground";
  return (
    <span className={cn(pill, tone)} title={t("detection.score")}>
      {t("detection.ai", { score: status.smoothed.toFixed(2) })}
    </span>
  );
}

export function DetectionBadge({ printerId }: { printerId: number }) {
  const q = usePrinterDetection(printerId, { poll: true });
  if (!q.data?.status) return null;
  return <DetectionBadgeView status={q.data.status} />;
}

/** Card variant: reads the shared summary poll instead of one query per printer. */
export function SharedDetectionBadge({ printerId }: { printerId: number }) {
  const q = useDetectionSummary();
  const status = q.data?.printers?.[String(printerId)];
  if (!status) return null;
  return <DetectionBadgeView status={status} />;
}
