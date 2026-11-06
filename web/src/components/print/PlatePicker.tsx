import { lazy, Suspense, useMemo } from "react";
import { ImageIcon, Pause } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { PlateOut, SourceMeta } from "@/api/types";
import { PlateSwatches } from "@/components/viewer/PlateFilaments";
import { plateLabel, plateThumbUrl, withSourceFallback } from "@/components/viewer/plates";
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
  plates: platesProp,
  value,
  onChange,
  allowAll = false,
  preview = true,
  source,
}: {
  blobHash: string | null | undefined;
  plates: PlateOut[];
  value: number | null;
  onChange: (plate: number | null) => void;
  allowAll?: boolean;
  preview?: boolean;
  /** MakerWorld provenance: its per-plate time/weight fill gaps in `plates`. */
  source?: SourceMeta | null;
}) {
  const { t } = useTranslation();
  const plates = useMemo(() => withSourceFallback(platesProp, source), [platesProp, source]);
  const selected = plates.find((p) => p.index === value);
  const card = (active: boolean) =>
    cn("w-40 shrink-0 space-y-2 rounded-lg border p-3 text-left", active ? "border-primary" : "border-border");
  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label={t("profiles.plateGroup")} className="flex gap-3 overflow-x-auto pb-2">
        {allowAll ? (
          <button
            type="button"
            role="radio"
            aria-checked={value == null}
            onClick={() => onChange(null)}
            className={card(value == null)}
          >
            <div className="flex aspect-square items-center justify-center rounded bg-muted text-xs text-muted-foreground">
              {t("profiles.allPlatesCount", { count: plates.length })}
            </div>
            <p className="text-xs text-muted-foreground">{t("profiles.allPlates")}</p>
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
                <img src={plateThumbUrl(blobHash, p.index)} alt={t("profiles.plateImgAlt", { index: p.index })} className="h-full w-full object-cover" />
              ) : (
                <ImageIcon className="size-8 text-muted-foreground" />
              )}
              {p.fits === false ? (
                <span className="absolute top-1 right-1 rounded bg-destructive px-1.5 py-0.5 text-[10px] font-medium text-destructive-foreground">
                  {t("profiles.tooLarge")}
                </span>
              ) : null}
            </div>
            <p className="text-xs text-muted-foreground">
              {plateLabel(p, t)}
              {p.prediction_s != null ? ` · ${humanizeDuration(p.prediction_s)}` : ""}
              {p.weight_g != null ? ` · ${Math.round(p.weight_g)} g` : ""}
            </p>
            <div className="flex items-center gap-1.5">
              <PlateSwatches plate={p} />
              {(p.colour_changes?.length ?? 0) > 0 ? (
                <span title={t("profiles.colourChangeCount", { count: p.colour_changes!.length })} className="text-[10px] text-muted-foreground">
                  <span aria-hidden>⇄</span>
                  <span className="sr-only">{t("profiles.colourChangeCount", { count: p.colour_changes!.length })}</span>
                </span>
              ) : null}
              {(p.pauses?.length ?? 0) > 0 ? (
                <span title={t("profiles.pauseHint")} className="text-muted-foreground">
                  <Pause aria-hidden data-testid="plate-pause" className="size-3" />
                  <span className="sr-only">{t("profiles.pauseCount", { count: p.pauses!.length })}</span>
                </span>
              ) : null}
            </div>
          </button>
        ))}
      </div>
      {allowAll && value == null ? (
        <p className="text-xs text-muted-foreground">{t("profiles.allPlatesNote")}</p>
      ) : null}
      {preview && blobHash && selected?.has_glb ? (
        <Suspense fallback={<div className="h-48 w-full animate-pulse rounded-lg bg-muted" />}>
          <PlatePreview blobHash={blobHash} plate={selected.index} />
        </Suspense>
      ) : null}
    </div>
  );
}
