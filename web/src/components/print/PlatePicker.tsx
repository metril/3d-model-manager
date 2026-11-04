import { lazy, Suspense } from "react";
import { ImageIcon } from "lucide-react";

import type { PlateOut } from "@/api/types";
import { plateLabel, plateThumbUrl } from "@/components/viewer/plates";
import { humanizeDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

const PlatePreview = lazy(() => import("@/components/print/PlatePreview"));

/**
 * Shared plate chooser (PrintDialog, SliceDialog): thumbnail cards, an
 * optional "All plates" card (value `null`), a badge on plates that don't
 * fit the bed, and a lazy GLB preview of the selected plate.
 */
export function PlatePicker({
  blobHash,
  plates,
  value,
  onChange,
  allowAll = false,
  preview = true,
}: {
  blobHash: string | null | undefined;
  plates: PlateOut[];
  value: number | null;
  onChange: (plate: number | null) => void;
  allowAll?: boolean;
  preview?: boolean;
}) {
  const selected = plates.find((p) => p.index === value);
  const card = (active: boolean) =>
    cn("w-40 shrink-0 space-y-2 rounded-lg border p-3 text-left", active ? "border-primary" : "border-border");
  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label="Plate" className="flex gap-3 overflow-x-auto pb-2">
        {allowAll ? (
          <button
            type="button"
            role="radio"
            aria-checked={value == null}
            onClick={() => onChange(null)}
            className={card(value == null)}
          >
            <div className="flex aspect-square items-center justify-center rounded bg-muted text-xs text-muted-foreground">
              {plates.length} plates
            </div>
            <p className="text-xs text-muted-foreground">All plates</p>
          </button>
        ) : null}
        {plates.map((p) => (
          <button
            type="button"
            role="radio"
            aria-checked={value === p.index}
            key={p.index}
            onClick={() => onChange(p.index)}
            className={card(value === p.index)}
          >
            <div className="relative flex aspect-square items-center justify-center overflow-hidden rounded bg-muted">
              {p.thumbnail_available && blobHash ? (
                <img src={plateThumbUrl(blobHash, p.index)} alt={`Plate ${p.index}`} className="h-full w-full object-cover" />
              ) : (
                <ImageIcon className="size-8 text-muted-foreground" />
              )}
              {p.fits === false ? (
                <span className="absolute top-1 right-1 rounded bg-destructive px-1.5 py-0.5 text-[10px] font-medium text-destructive-foreground">
                  Too large
                </span>
              ) : null}
            </div>
            <p className="text-xs text-muted-foreground">
              {plateLabel(p)}
              {p.prediction_s != null ? ` · ${humanizeDuration(p.prediction_s)}` : ""}
              {p.weight_g != null ? ` · ${Math.round(p.weight_g)} g` : ""}
            </p>
          </button>
        ))}
      </div>
      {allowAll && value == null ? (
        <p className="text-xs text-muted-foreground">All plates are sliced, but only plate 1 prints.</p>
      ) : null}
      {preview && blobHash && selected?.has_glb ? (
        <Suspense fallback={<div className="h-48 w-full animate-pulse rounded-lg bg-muted" />}>
          <PlatePreview blobHash={blobHash} plate={selected.index} />
        </Suspense>
      ) : null}
    </div>
  );
}
