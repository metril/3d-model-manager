import { ImageIcon } from "lucide-react";

import { plateLabel, plateThumbUrl, selectablePlates } from "@/components/viewer/plates";
import { cn } from "@/lib/utils";
import type { PlateOut } from "@/api/types";

/** Viewer plate chooser: "All plates" plus one thumbnail chip per plate.
 * Renders nothing below two plates (no dead controls). */
export function PlateSelector({
  blobHash,
  plates,
  value,
  onChange,
}: {
  blobHash: string;
  plates: PlateOut[] | null | undefined;
  value: number | null;
  onChange: (plate: number | null) => void;
}) {
  const list = selectablePlates(plates);
  if (list.length === 0) return null;
  const chip = (active: boolean) =>
    cn(
      "pointer-events-auto flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-xs backdrop-blur-sm",
      active ? "border-primary bg-primary/15 text-foreground" : "border-border bg-background/80 text-muted-foreground",
    );
  return (
    <div role="radiogroup" aria-label="Plate" className="pointer-events-none flex max-w-full gap-1.5 overflow-x-auto px-2">
      <button type="button" role="radio" aria-checked={value == null} className={chip(value == null)} onClick={() => onChange(null)}>
        All plates
      </button>
      {list.map((p) => (
        <button
          key={p.index}
          type="button"
          role="radio"
          aria-checked={value === p.index}
          className={chip(value === p.index)}
          onClick={() => onChange(p.index)}
          title={p.fits === false ? "Does not fit the bed" : undefined}
        >
          {p.thumbnail_available ? (
            <img src={plateThumbUrl(blobHash, p.index)} alt="" className="size-5 rounded object-cover" />
          ) : (
            <ImageIcon className="size-4" />
          )}
          <span className="whitespace-nowrap">{plateLabel(p)}</span>
          {p.fits === false ? <span className="text-destructive">!</span> : null}
        </button>
      ))}
    </div>
  );
}
