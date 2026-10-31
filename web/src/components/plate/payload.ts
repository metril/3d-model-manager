import type { CombineRequest } from "@/api/combine";
import type { PlateItem } from "@/components/plate/poseStore";

/** `POST /models/{slug}/combine` body for the editor's current layout. */
export function buildManualRequest(
  items: PlateItem[],
  target: { profileId: number | null; bed: { x_mm: number; y_mm: number } },
  name: string,
): CombineRequest {
  return {
    layout: "manual",
    spacing_mm: 0,
    items: items.map((i) => ({
      file_id: i.file_id,
      copies: 1,
      x_mm: i.x_mm,
      y_mm: i.y_mm,
      rot_z_deg: i.rot_z_deg,
      scale: i.scale,
      copy_index: i.copy_index,
    })),
    printer_profile_id: target.profileId,
    bed: target.profileId == null ? target.bed : null,
    name: name.trim() || null,
  };
}

const key = (slug: string, fileIds: number[]) => `plate-layout:${slug}:${[...fileIds].sort((a, b) => a - b).join(",")}`;

export function loadLayout(slug: string, fileIds: number[]): PlateItem[] | null {
  try {
    const raw = localStorage.getItem(key(slug, fileIds));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PlateItem[];
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    const ids = new Set(fileIds);
    return parsed.every((i) => ids.has(i.file_id) && Number.isFinite(i.x_mm) && Number.isFinite(i.y_mm)) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveLayout(slug: string, fileIds: number[], items: PlateItem[]): void {
  try {
    localStorage.setItem(key(slug, fileIds), JSON.stringify(items));
  } catch {
    /* storage unavailable: the layout just isn't remembered */
  }
}

export function clearLayout(slug: string, fileIds: number[]): void {
  try {
    localStorage.removeItem(key(slug, fileIds));
  } catch {
    /* ignore */
  }
}
