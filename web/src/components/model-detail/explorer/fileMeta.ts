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
  if (meta.plate_count !== null) parts.push(`${meta.plate_count} plates`);
  if (meta.print_time_s !== null) parts.push(humanizeDuration(meta.print_time_s));
  if (meta.filament_g !== null) parts.push(`${Math.round(meta.filament_g)} g`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function fileMetaLine(file: FileOut): string | null {
  if (!file.meta) return null;
  if (file.kind === "mesh" || file.kind === "cad") return meshMetaLine(file.meta);
  if (file.kind === "sliced") return slicedMetaLine(file.meta);
  return null;
}

/** STL/OBJ/3MF meshes that finished storing can be laid out on one plate. */
export function isCombinable(file: FileOut): boolean {
  return file.verified_at !== null && (file.format === "stl" || file.format === "obj" || file.format === "3mf");
}

/** Docs the side pane can render inline. */
export function isPreviewable(file: FileOut): boolean {
  return file.kind === "doc" && (file.format === "pdf" || file.format === "md" || file.format === "txt");
}
