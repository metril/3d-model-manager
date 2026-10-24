/**
 * Obico failure-detection badge (Q10): current smoothed score, or
 * "detection paused" after repeated ML errors. Renders nothing while
 * detection is idle. Exported for the Q3 card's BadgeRow.
 */
import { useDetectionSummary, usePrinterDetection } from "@/api/detection";
import type { DetectionStatus } from "@/api/detection";
import { cn } from "@/lib/utils";

export function DetectionBadgeView({ status }: { status: DetectionStatus }) {
  if (status.detection_paused) {
    return (
      <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-600">
        detection paused
      </span>
    );
  }
  if (!status.active || status.smoothed == null) return null;
  const tone =
    status.state === "pause"
      ? "border-destructive/40 bg-destructive/10 text-destructive"
      : status.state === "warn"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-600"
        : "border-border text-muted-foreground";
  return (
    <span
      className={cn("rounded-full border px-2 py-0.5 text-xs", tone)}
      title="Failure detection score (smoothed)"
    >
      AI {status.smoothed.toFixed(2)}
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
