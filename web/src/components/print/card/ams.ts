import type { AmsTray } from "@/api/types";
import { normalizeHex } from "@/components/ui/filament-chip";

export function unitLabel(id: number): string {
  return id >= 128 ? "AMS HT" : `AMS ${id + 1}`;
}

/** Trays holding the same material + colour in more than one slot: the printer can fall back between them. */
export function hasBackup(trays: AmsTray[]): boolean {
  const seen = new Set<string>();
  for (const t of trays) {
    const type = t.tray_type ?? t.material;
    const color = normalizeHex(t.color);
    if (!type || !color) continue;
    const key = `${type}|${color}`;
    if (seen.has(key)) return true;
    seen.add(key);
  }
  return false;
}
