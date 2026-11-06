import i18n from "@/i18n";
import { humanizeDuration } from "@/lib/format";
import type { BlobMetaOut, FileOut } from "@/api/types";

/** `{triangle_count} tris · {dims_mm joined ×} mm · {volume_cm3} cm³`, skipping
 * any part whose source value is null. */
function meshMetaLine(meta: BlobMetaOut): string | null {
  const parts: string[] = [];
  if (meta.triangle_count !== null) parts.push(`${meta.triangle_count} tris`);
  if (meta.dims_mm !== null) parts.push(`${meta.dims_mm.map((d) => d.toFixed(1)).join(" × ")} mm`);
  if (meta.volume_cm3 !== null) parts.push(`${meta.volume_cm3.toFixed(1)} cm³`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** `{plate_count} plates · {humanizeDuration(print_time_s)} · {filament_g} g`,
 * skipping any part whose source value is null. */
function slicedMetaLine(meta: BlobMetaOut): string | null {
  const parts: string[] = [];
  if (meta.plate_count !== null) parts.push(i18n.t("profiles.plateCount", { count: meta.plate_count }));
  if (meta.print_time_s !== null) parts.push(humanizeDuration(meta.print_time_s));
  if (meta.filament_g !== null) parts.push(`${Math.round(meta.filament_g)} g`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function baseMetaLine(file: FileOut): string | null {
  if (!file.meta) return null;
  if (file.kind === "mesh" || file.kind === "cad") return meshMetaLine(file.meta);
  if (file.kind === "sliced") return slicedMetaLine(file.meta);
  return null;
}

/** "Printers: P1S, A1 mini +12 · 8 plates" from a MakerWorld profile's `source_meta`. */
export function profileMetaLine(file: FileOut): string | null {
  const sm = file.source_meta;
  if (!sm) return null;
  const names = [...new Set((sm.compat ?? []).map((c) => c.product || c.model).filter(Boolean))] as string[];
  const parts: string[] = [];
  if (names.length) {
    const shown = names.slice(0, 3).join(", ");
    parts.push(`Printers: ${shown}${names.length > 3 ? ` +${names.length - 3}` : ""}`);
  }
  const plates = sm.plates?.length ?? 0;
  if (plates) parts.push(i18n.t("profiles.plateCount", { count: plates }));
  return parts.length ? parts.join(" · ") : null;
}

export function fileMetaLine(file: FileOut): string | null {
  return [baseMetaLine(file), profileMetaLine(file)].filter(Boolean).join(" · ") || null;
}

/** STL/OBJ/3MF meshes that finished storing can be laid out on one plate. */
export function isCombinable(file: FileOut): boolean {
  return file.verified_at !== null && (file.format === "stl" || file.format === "obj" || file.format === "3mf");
}

/** Docs the side pane can render inline. */
export function isPreviewable(file: FileOut): boolean {
  return file.kind === "doc" && (file.format === "pdf" || file.format === "md" || file.format === "txt");
}
