import type { DockviewApi } from "dockview-react";

import { LEFT_PANELS, RIGHT_PANELS, PANEL_TITLES, type PanelId } from "./panelMeta";

const LEFT_WIDTH = 0.62;
const VIEWER_HEIGHT = 0.62;
/** Relative heights of the right-hand stack, in `RIGHT_PANELS` order. */
const RIGHT_WEIGHTS: Partial<Record<PanelId, number>> = {
  gcodeProfiles: 1,
  printHistory: 1,
  filesDocs: 2,
  revisions: 1,
  notes: 1,
  printTips: 1,
  specs: 1.4,
};

/** The three cards under the viewer share one tabbed group. */
const LEFT_TABS: readonly PanelId[] = ["description", "tagsLinks", "related"];

function addPanel(api: DockviewApi, id: PanelId, position?: Parameters<DockviewApi["addPanel"]>[0]["position"]) {
  return api.addPanel({
    id,
    component: id,
    title: PANEL_TITLES[id],
    // The 3D viewer keeps its DOM (and GL context) alive when its tab is
    // hidden or the group is re-parented.
    renderer: id === "viewer" ? "always" : undefined,
    position,
  });
}

/** Today's two-column arrangement: viewer over a tabbed Description / Tags /
 * Related group on the left (~62%); the seven cards stacked on the right. */
export function buildDefaultLayout(api: DockviewApi): void {
  api.clear();
  const viewer = addPanel(api, "viewer");
  const description = addPanel(api, "description", { referencePanel: viewer, direction: "below" });
  addPanel(api, "tagsLinks", { referencePanel: description, direction: "within" });
  addPanel(api, "related", { referencePanel: description, direction: "within" });
  description.api.setActive();

  let previous = addPanel(api, RIGHT_PANELS[0], { referencePanel: viewer, direction: "right" });
  for (const id of RIGHT_PANELS.slice(1)) {
    previous = addPanel(api, id, { referencePanel: previous, direction: "below" });
  }

  // Sizes are best-effort: a zero-sized (not yet laid out) container skips them.
  const { width, height } = api;
  if (width > 0 && height > 0) {
    viewer.api.setSize({ width: width * LEFT_WIDTH, height: height * VIEWER_HEIGHT });
    const total = RIGHT_PANELS.reduce((sum, id) => sum + (RIGHT_WEIGHTS[id] ?? 1), 0);
    for (const id of RIGHT_PANELS.slice(0, -1)) {
      api.getPanel(id)?.api.setSize({ height: (height * (RIGHT_WEIGHTS[id] ?? 1)) / total });
    }
  }
}

/** Re-adds one closed panel near where it lives in the default layout. */
export function addPanelAtDefault(api: DockviewApi, id: PanelId): void {
  const has = (other: PanelId) => api.getPanel(other) !== undefined && other !== id;
  const ref = (other: PanelId) => api.getPanel(other)!;

  if ((LEFT_TABS as readonly PanelId[]).includes(id)) {
    const sibling = LEFT_TABS.find(has);
    if (sibling) return void addPanel(api, id, { referencePanel: ref(sibling), direction: "within" });
    if (has("viewer")) return void addPanel(api, id, { referencePanel: ref("viewer"), direction: "below" });
  } else if (id === "viewer") {
    const below = LEFT_TABS.find(has);
    if (below) return void addPanel(api, id, { referencePanel: ref(below), direction: "above" });
  } else {
    const index = RIGHT_PANELS.indexOf(id);
    const before = [...RIGHT_PANELS.slice(0, index)].reverse().find(has);
    if (before) return void addPanel(api, id, { referencePanel: ref(before), direction: "below" });
    const after = RIGHT_PANELS.slice(index + 1).find(has);
    if (after) return void addPanel(api, id, { referencePanel: ref(after), direction: "above" });
    const anchor = LEFT_PANELS.find(has);
    if (anchor) return void addPanel(api, id, { referencePanel: ref(anchor), direction: "right" });
  }
  addPanel(api, id);
}
