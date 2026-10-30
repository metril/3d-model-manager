import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import type { BedFit } from "@/api/slicing";

/** One-line bed-fit verdict for the chosen recipe/printer; null when unknown. */
function bedFitMessage(fit: BedFit | undefined, t: TFunction): string | null {
  if (!fit || fit.status === "unknown") return null;
  if (fit.status === "fits") return t("bedFit.fits", { bed: fit.bed ? `${fit.bed[0]}×${fit.bed[1]}` : "" }).trim();
  if (fit.status === "rotated") return t("bedFit.rotated");
  if (fit.status !== "too_large") return null;
  const parts = Object.keys(fit.overflow ?? {}).map((axis) => {
    const idx = "xyz".indexOf(axis);
    const dim = fit.dims ? Math.round(fit.dims[idx] * 10) / 10 : fit.overflow[axis];
    const limit = axis === "z" ? fit.height : fit.bed ? fit.bed[idx] : null;
    return axis === "z"
      ? t("bedFit.tooLargeZ", { dim, limit })
      : t("bedFit.tooLargeXY", { dim, axis: axis.toUpperCase(), limit });
  });
  const text = parts.join("; ");
  return fit.rotated ? `${text} (${t("bedFit.needsRotate")})` : text;
}

export function BedFitChip({ fit }: { fit: BedFit | undefined }) {
  const { t } = useTranslation();
  const text = bedFitMessage(fit, t);
  if (!text) return null;
  const bad = fit?.status === "too_large";
  return (
    <span
      role={bad ? "alert" : "status"}
      data-testid="bed-fit-chip"
      className={`inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-xs ${
        bad ? "border-destructive text-destructive" : fit?.status === "rotated" ? "text-amber-600" : "text-muted-foreground"
      }`}
    >
      {text}
    </span>
  );
}
