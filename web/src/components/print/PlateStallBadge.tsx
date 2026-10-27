/** Warn chip: a queued print has waited >10 min on a plate check with no camera frame. */
import { CameraOffIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export function PlateStallBadge() {
  return (
    <Badge
      variant="outline"
      title="Plate check is waiting for a camera frame; the queued print is held. Check the camera."
      aria-label="Plate check stalled"
      className="gap-1 border-warning bg-warning/10 text-warning"
    >
      <CameraOffIcon className="size-3.5" />
      Plate check stalled
    </Badge>
  );
}
