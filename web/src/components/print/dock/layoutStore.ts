import type { DockviewApi, SerializedDockview } from "dockview-react";

import { stripPopouts } from "@/components/model-detail/dock/layoutStore";
import { PANEL_IDS, isPanelId, type PanelId } from "./panelMeta";

export const LAYOUT_KEY = "tdmm.printDashLayout.v1";
/** "1" = the customisable dock replaces the classic page. */
export const MODE_KEY = "tdmm.printDashDock.v1";
export const LAYOUT_VERSION = 1;
const SAVE_DEBOUNCE_MS = 300;

type Stored = { v: number; known: PanelId[]; layout: SerializedDockview };
export type LoadedLayout = { layout: SerializedDockview; unseen: PanelId[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Every leaf's views must be panels the layout declares. */
function viewsResolve(node: unknown, ids: string[]): boolean {
  if (!isRecord(node)) return false;
  if (node.type === "leaf") {
    const views = isRecord(node.data) ? node.data.views : undefined;
    return Array.isArray(views) && views.every((v) => typeof v === "string" && ids.includes(v));
  }
  return node.type === "branch" && Array.isArray(node.data) && node.data.every((child) => viewsResolve(child, ids));
}

/** Saved layout (+ registry panels added since it was saved); null when absent
 * or structurally/registry-invalid, so the caller falls back to the default. */
export function loadLayout(storage: Storage = localStorage): LoadedLayout | null {
  try {
    const raw = storage.getItem(LAYOUT_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.v !== LAYOUT_VERSION || !isRecord(parsed.layout)) return null;
    const layout = parsed.layout as unknown as SerializedDockview;
    if (!isRecord(layout.grid) || !isRecord(layout.grid.root) || !isRecord(layout.panels)) return null;
    const ids = Object.keys(layout.panels);
    if (ids.length === 0 || !ids.every(isPanelId)) return null;
    if (!viewsResolve(layout.grid.root, ids)) return null;
    const known = Array.isArray(parsed.known) ? (parsed.known as unknown[]).filter(isPanelId) : [...ids];
    return { layout, unseen: PANEL_IDS.filter((id) => !known.includes(id)) };
  } catch {
    return null;
  }
}

export function writeLayout(layout: SerializedDockview, storage: Storage = localStorage): void {
  try {
    const stored: Stored = { v: LAYOUT_VERSION, known: [...PANEL_IDS], layout: stripPopouts(layout) };
    storage.setItem(LAYOUT_KEY, JSON.stringify(stored));
  } catch {
    // Storage unavailable / full: the layout just doesn't persist.
  }
}

let timer: ReturnType<typeof setTimeout> | undefined;
let pending: SerializedDockview | undefined;

/** Debounced save so dragging a sash doesn't hammer storage. */
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

export function resetLayout(storage: Storage = localStorage): void {
  if (timer) clearTimeout(timer);
  timer = undefined;
  pending = undefined;
  try {
    storage.removeItem(LAYOUT_KEY);
  } catch {
    // ignore
  }
}

export function readDockMode(storage: Storage = localStorage): boolean {
  try {
    return storage.getItem(MODE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeDockMode(on: boolean, storage: Storage = localStorage): void {
  try {
    if (on) storage.setItem(MODE_KEY, "1");
    else storage.removeItem(MODE_KEY);
  } catch {
    // ignore
  }
}

/** Registry panels the dock doesn't currently host. */
export function missingPanels(api: Pick<DockviewApi, "panels">): PanelId[] {
  const present = new Set(api.panels.map((panel) => panel.id));
  return PANEL_IDS.filter((id) => !present.has(id));
}
