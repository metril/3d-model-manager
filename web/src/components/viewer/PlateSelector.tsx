import { useId, useMemo } from "react";
import { ImageIcon, Pause } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PlateSwatches } from "@/components/viewer/PlateFilaments";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  ALL_FILES,
  plateLabel,
  plateThumbUrl,
  plateTitle,
  plateTotal,
  plateTotals,
  preloadPlateGlb,
  selectablePlates,
  type ViewerPlateFile,
} from "@/components/viewer/plates";
import { useRovingRadioGroup } from "@/components/viewer/useRovingRadioGroup";
import { humanizeDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { PlateOut } from "@/api/types";

/** More plates than this collapse into a compact dropdown instead of chips. */
export const MAX_PLATE_CHIPS = 6;

/** Viewer chooser: a print-profile dropdown (every profile 3MF, plus "All
 * files" when other meshes exist) followed by plate chips for the selected
 * profile only -- "All plates" (2+ plates) and one chip per plate. A single
 * plate profile gets no chips (the dropdown already says "1 plate"). Beyond
 * `MAX_PLATE_CHIPS` plates the chips collapse into one compact select. */
export function PlateSelector({
  blobHash,
  plates,
  value,
  onChange,
  files,
  hasAllFiles,
  onFileChange,
}: {
  blobHash: string;
  plates: PlateOut[] | null | undefined;
  value: number | null;
  onChange: (plate: number | null) => void;
  /** All print profiles; the dropdown shows whenever there is at least one. */
  files?: ViewerPlateFile[];
  hasAllFiles?: boolean;
  onFileChange?: (blobHash: string) => void;
}) {
  const { t } = useTranslation();
  const labelId = useId();
  const valueId = useId();
  const list = useMemo(() => selectablePlates(plates), [plates]);
  const total = plateTotal(list);
  const options = useMemo(() => ["all", ...list.map((p) => String(p.index))], [list]);
  const { itemProps } = useRovingRadioGroup(options, value == null ? "all" : String(value), (o) => onChange(o === "all" ? null : Number(o)));
  const showPicker = !!files && files.length > 0 && !!onFileChange;
  if (!showPicker && list.length === 0) return null;
  const chip = (active: boolean) =>
    cn(
      "pointer-events-auto flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-xs backdrop-blur-sm",
      active ? "border-primary bg-primary/15 text-foreground" : "border-border bg-background/80 text-muted-foreground",
    );
  const chipLabel = (p: PlateOut) => plateLabel(p, t, total);
  const optionLabel = (f: ViewerPlateFile) =>
    f.plates.length > 0 ? t("profiles.profileOption", { title: f.label, plates: t("profiles.plateCount", { count: f.plates.length }) }) : f.label;
  const current = files?.find((f) => f.blobHash === blobHash);
  const currentPlates = current && current.plates.length > 0 ? t("profiles.plateCount", { count: current.plates.length }) : null;
  const optionBody = (f: ViewerPlateFile) => {
    const first = f.plates[0];
    const { seconds, grams } = plateTotals(f.plates);
    const extra = [seconds != null ? humanizeDuration(seconds) : null, grams != null ? `${Math.round(grams)} g` : null].filter(Boolean).join(" · ");
    return (
      <span className="flex min-w-0 items-center gap-2">
        {first?.thumbnail_available ? <img src={plateThumbUrl(f.blobHash, first.index)} alt="" className="size-6 shrink-0 rounded object-cover" /> : null}
        <span className="min-w-0 truncate">{optionLabel(f)}</span>
        <PlateSwatches plate={{ filaments: f.plates.flatMap((p) => p.filaments) }} className="size-2" />
        {extra ? <span className="shrink-0 text-xs text-muted-foreground">{extra}</span> : null}
      </span>
    );
  };
  const markers = (p: PlateOut) => {
    const changes = p.colour_changes?.length ?? 0;
    const pauses = p.pauses?.length ?? 0;
    return (
      <>
        {changes > 0 ? (
          <>
            <span aria-hidden className="text-[10px]">⇄</span>
            <span className="sr-only">{t("profiles.colourChangeCount", { count: changes })}</span>
          </>
        ) : null}
        {pauses > 0 ? (
          <>
            <Pause aria-hidden data-testid="plate-pause" className="size-3" />
            <span className="sr-only">{t("profiles.pauseCount", { count: pauses })}</span>
          </>
        ) : null}
        {p.fits === false ? (
          <>
            <span aria-hidden className="text-destructive">!</span>
            <span className="sr-only">{t("profiles.tooFar")}</span>
          </>
        ) : null}
      </>
    );
  };
  const preload = (p: PlateOut) => {
    if (p.has_glb) preloadPlateGlb(blobHash, p.index);
  };
  return (
    <div className="pointer-events-none flex max-w-full flex-wrap justify-center gap-1.5 px-2">
      {showPicker ? (
        <Select value={blobHash} onValueChange={onFileChange}>
          <span id={labelId} className="sr-only">{t("profiles.plateFile")}</span>
          <SelectTrigger aria-labelledby={`${labelId} ${valueId}`} title={current ? optionLabel(current) : undefined} className="pointer-events-auto h-7 w-auto max-w-72 shrink-0 gap-1.5 rounded-full bg-background/80 px-2.5 text-xs backdrop-blur-sm">
            {current ? (
              <SelectValue>
                <span id={valueId} className="flex min-w-0 items-baseline gap-1">
                  <span className="min-w-0 truncate">{current.label}</span>{" "}
                  {currentPlates ? <span className="shrink-0 whitespace-nowrap text-muted-foreground">· {currentPlates}</span> : null}
                </span>
              </SelectValue>
            ) : (
              <SelectValue />
            )}
          </SelectTrigger>
          <SelectContent>
            {files.map((f) => (
              <SelectItem key={f.blobHash} value={f.blobHash}>
                {optionBody(f)}
              </SelectItem>
            ))}
            {hasAllFiles ? <SelectItem value={ALL_FILES}>{t("profiles.allFiles")}</SelectItem> : null}
          </SelectContent>
        </Select>
      ) : null}
      {list.length > MAX_PLATE_CHIPS ? (
        <Select value={value == null ? "all" : String(value)} onValueChange={(v) => onChange(v === "all" ? null : Number(v))}>
          <SelectTrigger aria-label={t("profiles.plateGroup")} className="pointer-events-auto h-7 w-auto max-w-56 shrink-0 gap-1.5 rounded-full bg-background/80 px-2.5 text-xs backdrop-blur-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("profiles.allPlates")}</SelectItem>
            {list.map((p) => (
              <SelectItem key={p.index} value={String(p.index)} onPointerEnter={() => preload(p)} onFocus={() => preload(p)}>
                {chipLabel(p)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : list.length > 0 ? (
        <div role="radiogroup" aria-label={t("profiles.plateGroup")} className="pointer-events-none flex flex-wrap justify-center gap-1.5">
          <button type="button" {...itemProps("all")} className={chip(value == null)}>
            {t("profiles.allPlates")}
          </button>
          {list.map((p) => (
            <button
              key={p.index}
              type="button"
              {...itemProps(String(p.index))}
              className={chip(value === p.index)}
              title={plateTitle(p, t)}
              onPointerEnter={() => preload(p)}
              onFocus={() => preload(p)}
            >
              {p.thumbnail_available ? (
                <img src={plateThumbUrl(blobHash, p.index)} alt="" className="size-5 rounded object-cover" />
              ) : (
                <ImageIcon aria-hidden className="size-4" />
              )}
              <span className="whitespace-nowrap">{chipLabel(p)}</span>
              <PlateSwatches plate={p} className="size-2" />
              {markers(p)}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
