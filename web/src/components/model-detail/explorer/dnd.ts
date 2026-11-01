/** Drag-and-drop payload helpers for moving explorer files/folders onto folders. */
import type { DragItems } from "@/api/fileOps";
import { fileKey, folderKey } from "@/components/model-detail/explorer/selection";

export const DND_TYPE = "application/x-tdmm-explorer";

export function encodeDrag(items: DragItems): string {
  return JSON.stringify(items);
}

export function decodeDrag(raw: string): DragItems | null {
  try {
    const v = JSON.parse(raw) as Partial<DragItems>;
    if (!Array.isArray(v.fileIds) || !Array.isArray(v.folders)) return null;
    const fileIds = v.fileIds.filter((n): n is number => typeof n === "number");
    const folders = v.folders.filter((p): p is string => typeof p === "string");
    return fileIds.length + folders.length > 0 ? { fileIds, folders } : null;
  } catch {
    return null;
  }
}

/** Splits selection keys (`file:<id>` / `folder:<path>`) back into ids and folder paths. */
export function itemsFromKeys(keys: Iterable<string>): DragItems {
  const items: DragItems = { fileIds: [], folders: [] };
  for (const key of keys) {
    if (key.startsWith("file:")) items.fileIds.push(Number(key.slice(5)));
    else if (key.startsWith("folder:")) items.folders.push(key.slice(7));
  }
  return items;
}

/** Only our own drags may be dropped on folders (OS file drops belong to the dropzone). */
export function isExplorerDrag(dt: Pick<DataTransfer, "types"> | null): boolean {
  return !!dt && Array.from(dt.types).includes(DND_TYPE);
}

/** What a drag of `item` carries: the whole selection when the item is part of it, else just the item. */
export function dragItemsFor(
  item: { kind: "file"; id: number } | { kind: "folder"; path: string },
  selected: DragItems,
  isSelected: (key: string) => boolean,
): DragItems {
  const key = item.kind === "file" ? fileKey(item.id) : folderKey(item.path);
  if (isSelected(key)) return selected;
  return item.kind === "file" ? { fileIds: [item.id], folders: [] } : { fileIds: [], folders: [item.path] };
}
