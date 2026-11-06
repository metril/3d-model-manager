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

/** "Colour change at 3.0 mm → ●" lines; nothing when the plate has none.
 * One non-wrapping row per change: the swatch stays glued to the arrow and
 * the text truncates (tooltip carries the full note) when space is tight. */
export function ColourChangeNote({ plate }: { plate: PlateOut }) {
  const { t } = useTranslation();
  const changes = plate.colour_changes ?? [];
  if (changes.length === 0) return null;
  return (
    <div className="space-y-0.5 text-[10px] text-muted-foreground">
      {changes.map((c, i) => {
        const text = `${t("profiles.colourChange", { z: c.z.toFixed(1) })} →`;
        return (
          <p key={i} data-testid="colour-change" className="inline-flex max-w-full items-center gap-1 whitespace-nowrap" title={`${text} ${c.colour}`}>
            <span className="min-w-0 truncate">{text}</span>
            <span className="size-2.5 shrink-0 rounded-full border border-border/60" style={{ backgroundColor: c.colour }} />
          </p>
        );
      })}
    </div>
  );
}
