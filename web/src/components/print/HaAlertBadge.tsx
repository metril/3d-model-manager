/**
 * The `badge` slot filled on a `PrinterCard`/the printer detail page header
 * when `item.ha_alert` is set (P8 Task 9): an amber `Siren` chip, labelled
 * "Held" when the alert is ALSO holding the dispatcher off this printer
 * (`ha_held`), "Alert" when it's merely reported. The reason (`ha_alert`'s
 * text) is both the hover tooltip and part of the aria-label, so it's
 * available to screen readers too, not just sighted hover.
 */
import { SirenIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export function HaAlertBadge({ reason, held }: { reason: string; held: boolean }) {
  const label = held ? "Held" : "Alert";
  return (
    <Badge
      variant="outline"
      title={reason}
      aria-label={`${label}: ${reason}`}
      className="gap-1 border-warning bg-warning/10 text-warning"
    >
      <SirenIcon className="size-3.5" />
      {label}
    </Badge>
  );
}
