import { ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PlateSwatches } from "@/components/viewer/PlateFilaments";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ALL_FILES, plateThumbUrl, plateTitle, selectablePlates, type ViewerPlateFile } from "@/components/viewer/plates";
import { cn } from "@/lib/utils";
import type { PlateOut } from "@/api/types";

/** Viewer chooser: a print-profile dropdown (every profile 3MF, plus "All
 * files" when other meshes exist) followed by plate chips for the selected
 * profile only -- "All plates" (2+ plates) and one chip per plate. A single
 * plate profile gets no chips (the dropdown already says "1 plate"). */
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
  const list = selectablePlates(plates);
  const showPicker = !!files && files.length > 0 && !!onFileChange;
  if (!showPicker && list.length === 0) return null;
  const chip = (active: boolean) =>
    cn(
      "pointer-events-auto flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-xs backdrop-blur-sm",
      active ? "border-primary bg-primary/15 text-foreground" : "border-border bg-background/80 text-muted-foreground",
    );
  const chipLabel = (p: PlateOut) => {
    const base = t("profiles.plateOf", { index: p.index, total: list.length });
    const name = p.name?.trim();
    return name && name.toLowerCase() !== `plate ${p.index}` ? `${base} · ${name}` : base;
  };
  const optionLabel = (f: ViewerPlateFile) =>
    f.plates.length > 0 ? t("profiles.profileOption", { title: f.label, plates: t("profiles.plateCount", { count: f.plates.length }) }) : f.label;
  return (
    <div className="pointer-events-none flex max-w-full gap-1.5 overflow-x-auto px-2">
      {showPicker ? (
        <Select value={blobHash} onValueChange={onFileChange}>
          <SelectTrigger aria-label={t("profiles.plateFile")} className="pointer-events-auto h-7 w-auto max-w-64 shrink-0 gap-1.5 rounded-full bg-background/80 px-2.5 text-xs backdrop-blur-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {files.map((f) => (
              <SelectItem key={f.blobHash} value={f.blobHash}>
                {optionLabel(f)}
              </SelectItem>
            ))}
            {hasAllFiles ? <SelectItem value={ALL_FILES}>{t("profiles.allFiles")}</SelectItem> : null}
          </SelectContent>
        </Select>
      ) : null}
      {list.length > 0 ? (
        <div role="radiogroup" aria-label="Plate" className="flex gap-1.5">
          <button type="button" role="radio" aria-checked={value == null} className={chip(value == null)} onClick={() => onChange(null)}>
            {t("profiles.allPlates")}
          </button>
          {list.map((p) => (
            <button
              key={p.index}
              type="button"
              role="radio"
              aria-checked={value === p.index}
              className={chip(value === p.index)}
              onClick={() => onChange(p.index)}
              title={plateTitle(p, t)}
            >
              {p.thumbnail_available ? (
                <img src={plateThumbUrl(blobHash, p.index)} alt="" className="size-5 rounded object-cover" />
              ) : (
                <ImageIcon className="size-4" />
              )}
              <span className="whitespace-nowrap">{chipLabel(p)}</span>
              <PlateSwatches plate={p} className="size-2" />
              {(p.colour_changes?.length ?? 0) > 0 ? <span aria-hidden className="text-[10px]">⇄</span> : null}
              {p.fits === false ? <span className="text-destructive">!</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
