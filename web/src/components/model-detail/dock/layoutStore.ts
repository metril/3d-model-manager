import type { DockviewApi, SerializedDockview } from "dockview-react";

import { PANEL_IDS, isPanelId, type PanelId } from "./panelMeta";

export const LAYOUT_KEY = "tdmm.modelDetailLayout.v1";
export const LAYOUT_VERSION = 1;
const SAVE_DEBOUNCE_MS = 300;

type Stored = { v: number; known: PanelId[]; layout: SerializedDockview };
type LeafData = { id?: string; views?: string[]; activeView?: string };
type GridNode = { type: "leaf" | "branch"; data: unknown };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function leaves(node: unknown, out: LeafData[] = []): LeafData[] {
  if (!isRecord(node)) return out;
  const n = node as unknown as GridNode;
  if (n.type === "leaf" && isRecord(n.data)) out.push(n.data as LeafData);
  else if (n.type === "branch" && Array.isArray(n.data)) n.data.forEach((child) => leaves(child, out));
  return out;
}

/** Structural + registry validation. Anything unexpected (wrong version,
 * missing grid, a panel id the registry doesn't know) rejects the whole
 * layout so the caller falls back to the default instead of half-restoring. */
export function parseLayout(raw: string | null): SerializedDockview | null {
  return parseStored(raw)?.layout ?? null;
}

export type LoadedLayout = { layout: SerializedDockview; unseen: PanelId[] };

function parseStored(raw: string | null): { layout: SerializedDockview; known: PanelId[] } | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || parsed.v !== LAYOUT_VERSION || !isRecord(parsed.layout)) return null;
  const layout = parsed.layout as unknown as SerializedDockview;
  if (!isRecord(layout.grid) || !isRecord(layout.grid.root) || !isRecord(layout.panels)) return null;
  const ids = Object.keys(layout.panels);
  if (ids.length === 0 || !ids.every(isPanelId)) return null;
  for (const leaf of leaves(layout.grid.root)) {
    if (!Array.isArray(leaf.views) || !leaf.views.every((id) => typeof id === "string" && (ids as string[]).includes(id))) {
      return null;
    }
  }
  const known = Array.isArray(parsed.known) ? (parsed.known as unknown[]).filter(isPanelId) : [...ids];
  return { layout, known };
}

/** Popout windows never survive a reload (the browser closes them), so a saved
 * layout must not reference them. dockview keeps a popped-out group in the grid
 * as a hidden placeholder (`visible: false`); un-hide it, and make sure every
 * popped-out panel sits in exactly one docked group (the placeholder, else the
 * first docked group). Orphans with no docked group are dropped and re-added by
 * `missingPanels`. */
export function stripPopouts(layout: SerializedDockview): SerializedDockview {
  const popouts = layout.popoutGroups ?? [];
  const { popoutGroups: _drop, ...rest } = layout;
  void _drop;
  if (popouts.length === 0) return layout;
  const next: SerializedDockview = JSON.parse(JSON.stringify(rest));
  const docked = leaves(next.grid.root);
  const placed = new Set(docked.flatMap((leaf) => leaf.views ?? []));
  for (const popout of popouts) {
    const poppedLeaves = popout.grid ? leaves(popout.grid.root) : popout.data ? [popout.data as LeafData] : [];
    const target = docked.find((leaf) => leaf.id === popout.gridReferenceGroup) ?? docked[0];
    for (const leaf of poppedLeaves) {
      for (const view of leaf.views ?? []) {
        if (placed.has(view)) continue;
        if (target) {
          target.views = [...(target.views ?? []), view];
          placed.add(view);
        } else delete next.panels[view];
      }
    }
  }
  unhide(next.grid.root);
  for (const leaf of docked) leaf.activeView ??= leaf.views?.[0];
  return next;
}

function unhide(node: unknown): void {
  if (!isRecord(node)) return;
  if (node.visible === false) node.visible = true;
  if (node.type === "branch" && Array.isArray(node.data)) node.data.forEach(unhide);
}

/** Saved layout plus the registry panels that did not exist when it was saved
 * (new panels get added on load; panels the user closed stay closed). */
export function loadLayout(storage: Storage = localStorage): LoadedLayout | null {
  try {
    const stored = parseStored(storage.getItem(LAYOUT_KEY));
    if (!stored) return null;
    return { layout: stored.layout, unseen: PANEL_IDS.filter((id) => !stored.known.includes(id)) };
  } catch {
    return null;
  }
}

export function writeLayout(layout: SerializedDockview, storage: Storage = localStorage): void {
  try {
    const docked = stripPopouts(layout);
    // Everything popped out and nothing docked: keep the previous save.
    if (leaves(docked.grid.root).length === 0) return;
    const stored: Stored = { v: LAYOUT_VERSION, known: [...PANEL_IDS], layout: docked };
    storage.setItem(LAYOUT_KEY, JSON.stringify(stored));
  } catch {
    // Storage unavailable / full: the layout just doesn't persist.
  }
}

let timer: ReturnType<typeof setTimeout> | undefined;
let pending: SerializedDockview | undefined;

/** Debounced save (300ms trailing) so dragging a sash doesn't hammer storage. */
export function saveLayout(layout: SerializedDockview): void {
  pending = layout;
  if (timer) clearTimeout(timer);
  timer = setTimeout(flushLayout, SAVE_DEBOUNCE_MS);
}

export function flushLayout(): void {
  if (timer) clearTimeout(timer);
  timer = undefined;
  if (pending) writeLayout(pending);
  pending = undefined;
}

export function cancelPendingSave(): void {
  if (timer) clearTimeout(timer);
  timer = undefined;
  pending = undefined;
}

export function resetLayout(storage: Storage = localStorage): void {
  cancelPendingSave();
  try {
    storage.removeItem(LAYOUT_KEY);
  } catch {
    // ignore
  }
}

/** Registry panels the dock doesn't currently host (closed by the user, or
 * added to the registry after the layout was saved). */
export function missingPanels(api: Pick<DockviewApi, "panels">): PanelId[] {
  const present = new Set(api.panels.map((panel) => panel.id));
  return PANEL_IDS.filter((id) => !present.has(id));
}
