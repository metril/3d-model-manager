/** Pure list helpers shared by the Slice and Print dialogs: search, ordering,
 * online/offline split, and loaded-tray filtering. */

const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

/** Case-insensitive name filter; a blank query keeps everything. */
export function filterByName<T extends { name: string }>(items: T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  return q === "" ? items : items.filter((i) => i.name.toLowerCase().includes(q));
}

/** Compatible rows first (the printer's own presets float up), then natural
 * name order ("PA6" before "PA12"). Stable; does not mutate. */
export function sortPresets<T extends { name: string; compatible?: boolean | null }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => Number(b.compatible !== false) - Number(a.compatible !== false) || collator.compare(a.name, b.name),
  );
}

/** Splits printers by liveness. `onlineIds == null` (not loaded yet) treats
 * everything as online so nothing is hidden while the poll is in flight. */
export function splitOnline<T extends { id: number }>(
  printers: T[],
  onlineIds: ReadonlySet<number> | null,
): { online: T[]; offline: T[] } {
  if (onlineIds == null) return { online: printers, offline: [] };
  return {
    online: printers.filter((p) => onlineIds.has(p.id)),
    offline: printers.filter((p) => !onlineIds.has(p.id)),
  };
}

/** A tray counts as loaded when it reports a material, a colour or a spool. */
export function isLoadedTray(t: { material?: string | null; color?: string | null; spool?: unknown }): boolean {
  return Boolean(t.material || t.color || t.spool);
}
