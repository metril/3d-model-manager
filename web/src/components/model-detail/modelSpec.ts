import type { BlobFormat, BlobMetaOut, ModelDetail } from "@/api/types";
import type { SpecItem } from "@/components/ui/spec-row";

export interface ModelFilament {
  color: string | null;
  material: string | null;
}

/** Distinct (color, material) filament pairs across the current revision's
 * sliced-plate metadata — the source for the header's filament chips. Pairs
 * with neither a color nor a material are ignored. */
export function modelFilaments(model: ModelDetail): ModelFilament[] {
  const files = model.current_revision?.files ?? [];
  const seen = new Map<string, ModelFilament>();
  for (const file of files) {
    for (const plate of file.meta?.plates ?? []) {
      // Unsliced plates carry colour-only filaments (type null): keep chips sliced-only.
      if (plate.filaments.every((f) => f.type == null)) continue;
      for (const filament of plate.filaments) {
        if (!filament.color && !filament.type) continue;
        const key = `${filament.color ?? ""}|${filament.type ?? ""}`;
        if (!seen.has(key)) seen.set(key, { color: filament.color, material: filament.type });
      }
    }
  }
  return [...seen.values()];
}

/** Distinct blob formats present in the current revision, in first-seen order. */
export function revisionFormats(model: ModelDetail): BlobFormat[] {
  const files = model.current_revision?.files ?? [];
  return [...new Set(files.map((file) => file.format))];
}

/** Mesh/CAD geometry facts for the Specs tab's `SpecRow` -- triangle count,
 * bounding-box dims, volume -- skipping any field the backend never
 * extracted for this file. */
export function meshSpecItems(meta: BlobMetaOut): SpecItem[] {
  const items: SpecItem[] = [];
  if (meta.dims_mm) items.push({ label: `${meta.dims_mm.map((d) => d.toFixed(1)).join(" × ")} mm` });
  if (meta.volume_cm3 !== null) items.push({ label: `${meta.volume_cm3.toFixed(1)} cm³` });
  if (meta.triangle_count !== null) items.push({ label: `${meta.triangle_count.toLocaleString()} tris` });
  if (meta.is_watertight !== null) items.push({ label: meta.is_watertight ? "Watertight" : "Not watertight" });
  return items;
}

/** Sliced-file facts for the Specs tab's `SpecRow` -- slicer, layer height,
 * infill, plate count -- the same fields `PlatePanel`'s header/meta lines
 * show, reused here so the Specs tab doesn't need its own copy. */
export function slicedSpecItems(meta: BlobMetaOut): SpecItem[] {
  const items: SpecItem[] = [];
  if (meta.slicer) items.push({ label: meta.slicer });
  if (meta.printer_model) items.push({ label: meta.printer_model });
  if (meta.layer_height !== null) items.push({ label: `${meta.layer_height} mm layers` });
  if (meta.infill_pct !== null) items.push({ label: `${meta.infill_pct}% infill` });
  if (meta.plate_count !== null) items.push({ label: `${meta.plate_count} plates` });
  return items;
}
