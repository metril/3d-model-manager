import { useTranslation } from "react-i18next";

import type { PlateOut } from "@/api/types";

/** Filament colour swatches for a plate (deduped by colour, order kept). */
export function PlateSwatches({ plate, className = "size-2.5" }: { plate: PlateOut; className?: string }) {
  const colours = [...new Set(plate.filaments.map((f) => f.color).filter((c): c is string => !!c))];
  if (colours.length === 0) return null;
  return (
    <span className="inline-flex items-center gap-0.5" aria-hidden>
      {colours.map((c) => (
        <span key={c} className={`${className} shrink-0 rounded-full border border-border/60`} style={{ backgroundColor: c }} />
      ))}
    </span>
  );
}

/** "Colour change at 3.0 mm → ●" lines; nothing when the plate has none. */
export function ColourChangeNote({ plate }: { plate: PlateOut }) {
  const { t } = useTranslation();
  const changes = plate.colour_changes ?? [];
  if (changes.length === 0) return null;
  return (
    <div className="space-y-0.5 text-[10px] text-muted-foreground">
      {changes.map((c, i) => (
        <p key={i} className="flex items-center gap-1">
          {t("profiles.colourChange", { z: c.z.toFixed(1) })} →
          <span className="size-2.5 rounded-full border border-border/60" style={{ backgroundColor: c.colour }} />
        </p>
      ))}
    </div>
  );
}
