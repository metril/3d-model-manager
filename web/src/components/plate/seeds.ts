import type { CombineItem } from "@/api/combine";
import { partKey, type PlateItem } from "@/components/plate/poseStore";

/** One request item per (part, orientation, scale) so tilted copies stay tilted. */
export function groupItems(items: PlateItem[]): CombineItem[] {
  const groups = new Map<string, CombineItem>();
  for (const i of items) {
    const k = `${partKey(i)}|${i.orient.join(",")}|${i.scale}`;
    const g = groups.get(k);
    if (g) g.copies += 1;
    else {
      groups.set(k, {
        file_id: i.file_id,
        ...(i.object_index == null ? {} : { object_index: i.object_index }),
        copies: 1,
        scale: i.scale,
        orient: i.orient,
      });
    }
  }
  return [...groups.values()];
}

/**
 * Whether bed-local object seeds could land on top of each other: a file with
 * more than one plate stacks every plate on the one bed, and several split
 * files (or a split file plus whole-file parts) share it too.
 */
export function seedsMayOverlap(
  objectsByFile: Map<number, { plate: number | null }[]>,
  wholeFileCount: number,
): boolean {
  if (objectsByFile.size > 1 || (objectsByFile.size === 1 && wholeFileCount > 0)) return true;
  for (const list of objectsByFile.values()) {
    if (new Set(list.map((o) => o.plate).filter((p) => p != null)).size > 1) return true;
  }
  return false;
}

/** Keep at most `room` items; `truncated` says whether any were dropped. */
export function capItems<T>(items: T[], room: number): { items: T[]; truncated: boolean } {
  const keep = Math.max(0, room);
  return { items: items.slice(0, keep), truncated: items.length > keep };
}
