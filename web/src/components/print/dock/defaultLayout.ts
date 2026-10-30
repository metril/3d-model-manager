import type { DockviewApi, IDockviewPanel } from "dockview-react";

import { DEFAULT_GROUPS, PANEL_TITLES, type DefaultGroup, type PanelId } from "./panelMeta";

const MAIN_WIDTH = 0.72;
const MAIN_HEIGHT = 0.72;
const RAIL_TOP_HEIGHT = 0.5;

type Direction = "above" | "below" | "left" | "right";
type Position = { referencePanel: IDockviewPanel; direction: Direction | "within" };

function addPanel(api: DockviewApi, id: PanelId, position?: Position) {
  return api.addPanel({ id, component: id, title: PANEL_TITLES[id], position });
}

/** The classic page as dock groups: printers (left), queue over archives
 * (right rail), the extras tabbed under the printers. */
export function buildDefaultLayout(api: DockviewApi): void {
  api.clear();
  const { main, railTop, railBottom, extras } = DEFAULT_GROUPS;
  const printers = addPanel(api, main[0]);
  const queue = addPanel(api, railTop[0], { referencePanel: printers, direction: "right" });
  addPanel(api, railBottom[0], { referencePanel: queue, direction: "below" });
  const first = addPanel(api, extras[0], { referencePanel: printers, direction: "below" });
  for (const id of extras.slice(1)) addPanel(api, id, { referencePanel: first, direction: "within" });
  printers.api.setActive();
  const { width, height } = api;
  if (width > 0 && height > 0) {
    printers.api.setSize({ width: width * MAIN_WIDTH, height: height * MAIN_HEIGHT });
    queue.api.setSize({ height: height * RAIL_TOP_HEIGHT });
  }
}

/** Re-adds one closed panel next to a sibling from its default group, else
 * beside whatever is docked. */
export function addPanelAtDefault(api: DockviewApi, id: PanelId): void {
  const has = (other: PanelId) => other !== id && api.getPanel(other) !== undefined;
  const group = (Object.keys(DEFAULT_GROUPS) as DefaultGroup[]).find((g) =>
    (DEFAULT_GROUPS[g] as readonly PanelId[]).includes(id),
  )!;
  const sibling = (DEFAULT_GROUPS[group] as readonly PanelId[]).find(has);
  if (sibling) return void addPanel(api, id, { referencePanel: api.getPanel(sibling)!, direction: "within" });
  const anchor = api.panels[0];
  addPanel(api, id, anchor ? { referencePanel: anchor, direction: "right" } : undefined);
}
