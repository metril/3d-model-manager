import { Pause } from "lucide-react";
import { useTranslation } from "react-i18next";

import { safeColour } from "@/components/viewer/plates";
import type { PlateOut } from "@/api/types";

/** Filament colour swatches for a plate (deduped by colour, order kept). */
export function PlateSwatches({ plate, className = "size-2.5" }: { plate: Pick<PlateOut, "filaments">; className?: string }) {
  const colours = [...new Set(plate.filaments.map((f) => safeColour(f.color)).filter((c): c is string => !!c))];
  if (colours.length === 0) return null;
  return (
    <span className="inline-flex items-center gap-0.5" aria-hidden>
      {colours.map((c) => (
        <span key={c} className={`${className} shrink-0 rounded-full border border-border/60`} style={{ backgroundColor: c }} />
      ))}
    </span>
  );
}

/** Layer events under a plate: "Colour change at 3.0 mm → ●" and
 * "⏸ Pause at 2.2 mm". Each is a full-width row whose text may wrap, while the
 * "z mm → ●" tail is one nowrap span so the swatch never detaches or hides. */
export function ColourChangeNote({ plate }: { plate: PlateOut }) {
  const { t } = useTranslation();
  const changes = plate.colour_changes ?? [];
  const pauses = plate.pauses ?? [];
  if (changes.length === 0 && pauses.length === 0) return null;
  return (
    <div className="w-full space-y-0.5 text-[10px] leading-tight text-muted-foreground">
      {changes.map((c, i) => (
        <p key={`c${i}`} data-testid="colour-change" className="w-full" title={t("profiles.colourChangeTo", { z: c.z.toFixed(1), colour: c.colour })}>
          {t("profiles.colourChangeAt")}{" "}
          <span data-testid="colour-change-z" className="inline-flex items-center gap-1 whitespace-nowrap align-middle">
            {t("profiles.zMmTo", { z: c.z.toFixed(1) })}
            <span className="size-2.5 shrink-0 rounded-full border border-border/60" style={{ backgroundColor: safeColour(c.colour) }} />
          </span>
        </p>
      ))}
      {pauses.map((p, i) => (
        <p key={`p${i}`} data-testid="pause-note" className="flex w-full items-start gap-1" title={t("profiles.pauseHint")}>
          <Pause aria-hidden className="mt-px size-2.5 shrink-0" />
          <span>{t("profiles.pauseAt", { z: p.z.toFixed(1) })}</span>
        </p>
      ))}
    </div>
  );
}
