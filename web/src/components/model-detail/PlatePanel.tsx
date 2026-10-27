import { lazy, Suspense, useState } from "react";
import { ImageIcon, LayersIcon, LoaderCircleIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatCost, humanizeDuration } from "@/lib/format";
import type { FileOut, PlateOut } from "@/api/types";

// `gcode-preview` drives its own three.js/WebGL renderer (Global Constraints
// "BUNDLE RULE") — loaded only once someone actually asks to preview layers.
const GcodePreview = lazy(() => import("@/components/viewer/GcodePreview"));

/** `{printer_model} · {nozzle} mm nozzle · {layer_height} mm layers`, skipping
 * any part whose source value is null (Task 9 brief). */
function headerLine(file: FileOut): string | null {
  const meta = file.meta;
  if (!meta) return null;
  const parts: string[] = [];
  if (meta.printer_model) parts.push(meta.printer_model);
  if (meta.nozzle !== null) parts.push(`${meta.nozzle} mm nozzle`);
  if (meta.layer_height !== null) parts.push(`${meta.layer_height} mm layers`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** `{slicer} · {duration} · {filament_g} g · {layer_height} mm · {infill}%
 * infill · {filament_type}`, skipping any null part (R10-B). */
function metaLine(file: FileOut): string | null {
  const meta = file.meta;
  if (!meta) return null;
  const parts = [
    meta.slicer,
    meta.print_time_s !== null ? humanizeDuration(meta.print_time_s) : null,
    meta.filament_g !== null ? `${Math.round(meta.filament_g)} g` : null,
    meta.layer_height !== null ? `${meta.layer_height} mm layers` : null,
    meta.infill_pct !== null ? `${meta.infill_pct}% infill` : null,
    meta.filament_types && meta.filament_types.length > 0 ? meta.filament_types.join("/") : null,
  ];
  const filtered = parts.filter((part): part is string => Boolean(part));
  return filtered.length > 0 ? filtered.join(" · ") : null;
}

/** `Plate {index} · {humanizeDuration(prediction_s)} · {weight_g} g [·
 * Est. cost: N.NN]`, skipping any part whose source value is null (Task 9
 * brief; the cost segment is the server-computed `est_cost`, shown when
 * non-null). */
function plateCaption(plate: PlateOut): string {
  const parts = [
    `Plate ${plate.index}`,
    plate.prediction_s !== null ? humanizeDuration(plate.prediction_s) : null,
    plate.weight_g !== null ? `${Math.round(plate.weight_g)} g` : null,
    plate.est_cost !== null ? `Est. cost: ${formatCost(plate.est_cost)}` : null,
  ];
  return parts.filter((part): part is string => part !== null).join(" · ");
}

function PlateCard({ blobHash, plate, small = false }: { blobHash: string; plate: PlateOut; small?: boolean }) {
  return (
    <div className={`shrink-0 space-y-1.5 rounded-lg border border-border p-2 ${small ? "w-40" : "w-48"}`}>
      <div
        className={`flex items-center justify-center overflow-hidden rounded bg-muted ${small ? "h-14" : "aspect-square"}`}
      >
        {plate.thumbnail_available ? (
          <img
            src={`/api/blobs/${blobHash}/plates/${plate.index}/thumb`}
            alt={`Plate ${plate.index}`}
            className="h-full w-full object-cover"
          />
        ) : (
          <ImageIcon className="size-8 text-muted-foreground" />
        )}
      </div>
      <p className="text-xs text-muted-foreground">{plateCaption(plate)}</p>
      {plate.filaments.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {plate.filaments.map((filament, index) => (
            <span
              key={index}
              className="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5 text-[10px]"
            >
              {filament.color ? (
                <span
                  className="size-2 shrink-0 rounded-full border border-border/50"
                  style={{ backgroundColor: filament.color }}
                />
              ) : null}
              {filament.type ?? "Filament"}
              {filament.used_g !== null ? ` ${Math.round(filament.used_g)} g` : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Per-plate time/weight/filament breakdown for a sliced file (Task 9),
 * replacing the ViewerTab's earlier sliced-file placeholder card.
 *
 * `compact` (Phase 4 studio) renders just the plate-card strip -- no header/
 * meta lines, no "Preview layers" button -- for the thin strip
 * `StudioSurface` shows beneath the assembly viewer when the model has both
 * ready GLB parts AND sliced files, so neither view has to hide behind the
 * other. */
export function PlatePanel({
  file,
  compact = false,
  fill = false,
  autoPreview = false,
  noPreview = false,
}: {
  file: FileOut;
  compact?: boolean;
  /** Fill the host's height: canvas takes the remaining space, only the plate strip scrolls. */
  fill?: boolean;
  /** Mount the layer preview immediately instead of behind a button. */
  autoPreview?: boolean;
  /** Metadata + plate strip only; the layer preview lives in the main viewer. */
  noPreview?: boolean;
}) {
  const plates = file.meta?.plates ?? [];
  const header = headerLine(file);
  const meta = metaLine(file);
  const [previewOpen, setPreviewOpen] = useState(autoPreview);
  const info = [header, meta].filter(Boolean).join(" · ");
  const infoLine = (
    <>
      {header ? <span>{header}</span> : null}
      {header && meta ? " · " : null}
      {meta ? <span>{meta}</span> : null}
    </>
  );

  return (
    <div className={fill ? "flex h-full min-h-0 flex-col gap-2" : "space-y-3"}>
      {!compact && info ? (
        <p className={fill ? "shrink-0 truncate text-xs text-muted-foreground" : "text-sm text-muted-foreground"} title={info}>
          {infoLine}
        </p>
      ) : null}
      {!compact &&
        !noPreview &&
        (previewOpen ? (
          <div
            className={
              fill
                ? "min-h-0 flex-1 [&>div]:flex [&>div]:h-full [&>div]:min-h-0 [&>div]:flex-col [&>div]:space-y-2 [&_.aspect-video]:aspect-auto [&_.aspect-video]:min-h-0 [&_.aspect-video]:flex-1 [&>div>div:last-child]:shrink-0"
                : undefined
            }
          >
            <Suspense
              fallback={
                <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  Loading g-code preview…
                </div>
              }
            >
              <GcodePreview fileId={file.id} />
            </Suspense>
          </div>
        ) : (
          <Button variant="outline" size="sm" className="shrink-0 self-start" onClick={() => setPreviewOpen(true)}>
            <LayersIcon className="size-4" />
            Preview layers
          </Button>
        ))}
      {plates.length === 0 ? (
        !compact && (
          <p className="py-8 text-center text-sm text-muted-foreground">No plate details available yet.</p>
        )
      ) : (
        <div className={fill ? "flex shrink-0 gap-2 overflow-x-auto pb-1" : "flex gap-3 overflow-x-auto pb-2"}>
          {plates.map((plate) => (
            <PlateCard key={plate.index} blobHash={file.blob_hash} plate={plate} small={fill} />
          ))}
        </div>
      )}
    </div>
  );
}
