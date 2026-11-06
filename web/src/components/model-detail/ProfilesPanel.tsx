import { useMemo } from "react";
import { ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ColourChangeNote, PlateSwatches } from "@/components/viewer/PlateFilaments";
import {
  plateLabel,
  plateThumbUrl,
  profileFiles,
  selectablePlates,
  withSourceFallback,
  selectProfile,
  profileTitle,
  setSelectedPlate,
  useActiveViewerBlob,
  useSelectedPlate,
} from "@/components/viewer/plates";
import { humanizeDuration } from "@/lib/format";
import { plainText } from "@/lib/plainText";
import { cn } from "@/lib/utils";
import type { StudioSelection } from "@/components/model-detail/studioSelection";
import type { FileOut, ModelDetail, PlateOut } from "@/api/types";

function PlateTile({ file, plate, selectable, onPick }: { file: FileOut; plate: PlateOut; selectable: boolean; onPick: () => void }) {
  const { t } = useTranslation();
  const activeBlob = useActiveViewerBlob();
  const selected = useSelectedPlate(file.blob_hash);
  const active = selectable && activeBlob === file.blob_hash && selected === plate.index;
  const extra = [
    plate.prediction_s != null ? humanizeDuration(plate.prediction_s) : null,
    plate.weight_g != null ? `${Math.round(plate.weight_g)} g` : null,
  ].filter(Boolean);
  const onClick = () => {
    onPick();
    selectProfile(file.blob_hash);
    setSelectedPlate(file.blob_hash, active ? null : plate.index);
  };
  const Wrapper = selectable ? "button" : "div";
  return (
    <Wrapper
      {...(selectable ? { type: "button" as const, "aria-pressed": active, title: t("profiles.showPlate"), onClick } : {})}
      className={cn("w-32 shrink-0 space-y-1 rounded-lg border p-1.5 text-left", active ? "border-primary" : "border-border")}
    >
      <div className="flex aspect-square items-center justify-center overflow-hidden rounded bg-muted">
        {plate.thumbnail_available ? (
          <img src={plateThumbUrl(file.blob_hash, plate.index)} alt={t("profiles.plateImgAlt", { index: plate.index })} className="h-full w-full object-cover" />
        ) : (
          <ImageIcon className="size-6 text-muted-foreground" />
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {[plateLabel(plate, t), ...extra].join(" · ")}
        {plate.fits === false ? <span className="ml-1 font-medium text-destructive">{t("profiles.tooFar")}</span> : null}
      </p>
      <PlateSwatches plate={plate} />
      <ColourChangeNote plate={plate} />
    </Wrapper>
  );
}

/** Design -> Print profiles -> Plates: one card per 3MF (a print profile),
 * each with its own plates. Plates with a GLB select into the viewer. */
export function ProfilesPanel({
  model,
  selection,
  onSelectAssembly,
}: {
  model: ModelDetail;
  /** The studio's current selection: picking a profile while a sliced file is open returns to the model view. */
  selection?: StudioSelection;
  onSelectAssembly?: () => void;
}) {
  const { t } = useTranslation();
  const revisionFiles = model.current_revision?.files;
  const allFiles = useMemo(() => revisionFiles ?? [], [revisionFiles]);
  // One card per blob: duplicate files of the same 3MF content collapse into the first.
  const files = useMemo(() => {
    const seen = new Set<string>();
    return profileFiles(allFiles).filter((f) => !seen.has(f.blob_hash) && !!seen.add(f.blob_hash));
  }, [allFiles]);
  const activeBlob = useActiveViewerBlob();
  const slicedOpen = selection?.type === "file" && allFiles.some((f) => f.id === selection.id && f.kind === "sliced");
  const pick = () => {
    if (slicedOpen) onSelectAssembly?.();
  };
  if (files.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profiles.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {files.map((file) => {
          const sm = file.source_meta;
          const title = profileTitle(file);
          const plates = withSourceFallback(file.meta?.plates ?? [], sm);
          const ready = file.glb_status === "ok";
          const isActive = ready && activeBlob === file.blob_hash;
          const glbPlates = new Set(selectablePlates(plates).filter((p) => p.has_glb).map((p) => p.index));
          const desc = plainText(sm?.description);
          const author = plainText(sm?.author);
          return (
            <div
              key={file.id}
              role="group"
              aria-label={title}
              aria-current={isActive ? "true" : undefined}
              className={cn("space-y-2 rounded-lg border p-2", !ready && "opacity-60", isActive ? "border-primary bg-primary/5" : "border-border")}
            >
              <button
                type="button"
                aria-pressed={isActive}
                disabled={!ready}
                title={ready ? t("profiles.showProfile") : t("profiles.notReady", { status: file.glb_status ?? "none" })}
                onClick={() => {
                  pick();
                  selectProfile(file.blob_hash);
                }}
                className="block w-full min-w-0 rounded text-left focus-visible:outline-2 focus-visible:outline-primary"
              >
                <p className="truncate text-sm font-medium" title={file.rel_path}>{title}</p>
                <p className="text-xs text-muted-foreground">
                  {[author ? t("profiles.by", { author }) : null, plates.length >= 2 ? t("profiles.plateCount", { count: plates.length }) : null]
                    .filter(Boolean)
                    .join(" · ")}
                  {ready ? null : <span className="ml-1 text-warning">{t("profiles.notReady", { status: file.glb_status ?? "none" })}</span>}
                </p>
                {desc ? (
                  <p className="line-clamp-2 text-xs text-muted-foreground" title={desc}>{desc}</p>
                ) : null}
              </button>
              {plates.length > 0 && (
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {plates.map((p) => (
                    <PlateTile key={p.index} file={file} plate={p} selectable={ready && glbPlates.has(p.index)} onPick={pick} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
