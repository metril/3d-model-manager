import { ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PlateSwatches } from "@/components/viewer/PlateFilaments";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { plateThumbUrl, plateTitle, selectablePlates, type ViewerPlateFile } from "@/components/viewer/plates";
import { cn } from "@/lib/utils";
import type { PlateOut } from "@/api/types";

/** Viewer plate chooser: "All plates" plus one thumbnail chip per plate.
 * Renders nothing below two plates (no dead controls). */
export function PlateSelector({
  blobHash,
  plates,
  value,
  onChange,
  files,
  onFileChange,
}: {
  blobHash: string;
  plates: PlateOut[] | null | undefined;
  value: number | null;
  onChange: (plate: number | null) => void;
  /** All plated files; a file picker shows only when there is more than one. */
  files?: ViewerPlateFile[];
  onFileChange?: (blobHash: string) => void;
}) {
  const { t } = useTranslation();
  const list = selectablePlates(plates);
  if (list.length === 0) return null;
  const chip = (active: boolean) =>
    cn(
      "pointer-events-auto flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-xs backdrop-blur-sm",
      active ? "border-primary bg-primary/15 text-foreground" : "border-border bg-background/80 text-muted-foreground",
    );
  const current = files?.find((f) => f.blobHash === blobHash);
  const chipLabel = (p: PlateOut) => {
    const base = t("profiles.plateOf", { index: p.index, total: list.length });
    const name = p.name?.trim();
    return name && name.toLowerCase() !== `plate ${p.index}` ? `${base} · ${name}` : base;
  };
  return (
    <div role="radiogroup" aria-label="Plate" className="pointer-events-none flex max-w-full gap-1.5 overflow-x-auto px-2">
      {files && files.length > 1 && onFileChange ? (
        <Select value={blobHash} onValueChange={onFileChange}>
          <SelectTrigger aria-label={t("profiles.plateFile")} className="pointer-events-auto h-7 w-auto max-w-48 shrink-0 gap-1.5 rounded-full bg-background/80 px-2.5 text-xs backdrop-blur-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {files.map((f) => (
              <SelectItem key={f.blobHash} value={f.blobHash}>
                {f.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : current ? (
        <span className="pointer-events-auto flex h-7 max-w-48 shrink-0 items-center truncate rounded-full border border-border bg-background/80 px-2.5 text-xs font-medium backdrop-blur-sm" title={current.label}>
          {current.label} ·
        </span>
      ) : null}
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
  );
}
