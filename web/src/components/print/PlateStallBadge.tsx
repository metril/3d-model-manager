/** Warn chip: a queued print has waited >10 min on a plate check with no camera frame. */
import { CameraOffIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useTranslation } from "react-i18next";

export function PlateStallBadge() {
  const { t } = useTranslation();
  return (
    <Badge
      variant="outline"
      title={t("plateStall.title")}
      aria-label={t("plateStall.label")}
      className="gap-1 border-warning bg-warning/10 text-warning"
    >
      <CameraOffIcon className="size-3.5" />
      {t("plateStall.label")}
    </Badge>
  );
}
