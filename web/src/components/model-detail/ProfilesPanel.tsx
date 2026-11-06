import { ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ColourChangeNote, PlateSwatches } from "@/components/viewer/PlateFilaments";
import {
  clearSelectedPlate,
  fileLabel,
  plateLabel,
  plateThumbUrl,
  profileFiles,
  selectablePlates,
  setActiveViewerBlob,
  setSelectedPlate,
  useActiveViewerBlob,
  useSelectedPlate,
} from "@/components/viewer/plates";
import { humanizeDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { FileOut, ModelDetail, PlateOut } from "@/api/types";

function PlateTile({ file, plate, selectable }: { file: FileOut; plate: PlateOut; selectable: boolean }) {
  const { t } = useTranslation();
  const activeBlob = useActiveViewerBlob();
  const selected = useSelectedPlate(file.blob_hash);
  const active = selectable && activeBlob === file.blob_hash && selected === plate.index;
  const extra = [
    plate.prediction_s != null ? humanizeDuration(plate.prediction_s) : null,
    plate.weight_g != null ? `${Math.round(plate.weight_g)} g` : null,
  ].filter(Boolean);
  const onClick = () => {
    if (activeBlob !== file.blob_hash) {
      if (activeBlob) clearSelectedPlate(activeBlob);
      setActiveViewerBlob(file.blob_hash);
    }
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
          <img src={plateThumbUrl(file.blob_hash, plate.index)} alt={`Plate ${plate.index}`} className="h-full w-full object-cover" />
        ) : (
          <ImageIcon className="size-6 text-muted-foreground" />
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {[plateLabel(plate), ...extra].join(" · ")}
        {plate.fits === false ? <span className="ml-1 font-medium text-destructive">{t("profiles.tooFar")}</span> : null}
      </p>
      <PlateSwatches plate={plate} />
      <ColourChangeNote plate={plate} />
    </Wrapper>
  );
}

/** Design -> Print profiles -> Plates: one card per 3MF (a print profile),
 * each with its own plates. Plates with a GLB select into the viewer. */
export function ProfilesPanel({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  const files = profileFiles(model.current_revision?.files ?? []);
  if (files.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profiles.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {files.map((file) => {
          const sm = file.source_meta;
          const title = sm?.title?.trim() || fileLabel(file.rel_path);
          const plates = file.meta?.plates ?? [];
          const glbPlates = new Set(selectablePlates(plates).filter((p) => p.has_glb).map((p) => p.index));
          const desc = sm?.description?.trim();
          return (
            <div key={file.id} className="space-y-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium" title={file.rel_path}>{title}</p>
                <p className="text-xs text-muted-foreground">
                  {[sm?.author ? t("profiles.by", { author: sm.author }) : null, plates.length > 0 ? t("profiles.plateCount", { count: plates.length }) : null]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {desc ? (
                  <p className="line-clamp-2 text-xs text-muted-foreground" title={desc}>{desc}</p>
                ) : null}
              </div>
              {plates.length > 0 && (
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {plates.map((p) => (
                    <PlateTile key={p.index} file={file} plate={p} selectable={glbPlates.has(p.index)} />
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
