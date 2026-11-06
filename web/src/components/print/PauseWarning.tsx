import { PauseIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { PlateOut } from "@/api/types";

/** Confirm-step warning when the chosen plate (or any plate, for "All plates")
 * contains a manual-filament-swap pause. */
export function PauseWarning({ plates, plate }: { plates: PlateOut[]; plate: number | null }) {
  const { t } = useTranslation();
  const scoped = plate != null ? plates.filter((p) => p.index === plate) : plates;
  const paused = scoped.filter((p) => (p.pauses?.length ?? 0) > 0);
  if (paused.length === 0) return null;
  return (
    <p role="alert" data-testid="pause-warning" className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-2 text-sm">
      <PauseIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
      <span>
        {plate != null
          ? t("profiles.pausePlateWarn", { z: paused[0].pauses![0].z.toFixed(1) })
          : t("profiles.pauseAllWarn", { count: paused.length })}
      </span>
    </p>
  );
}
