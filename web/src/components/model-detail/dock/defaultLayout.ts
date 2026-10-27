import type { DockviewApi, IDockviewPanel } from "dockview-react";

import { DEFAULT_ACTIVE, DEFAULT_GROUPS, PANEL_TITLES, type DefaultGroup, type PanelId } from "./panelMeta";

const LEFT_WIDTH = 0.66;
const LEFT_TOP_HEIGHT = 0.78;
const RIGHT_TOP_HEIGHT = 0.5;

type Direction = "above" | "below" | "left" | "right";
type Position = { referencePanel: IDockviewPanel; direction: Direction | "within" };

function addPanel(api: DockviewApi, id: PanelId, position?: Position) {
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

/** Today's two-column arrangement as dock groups: the viewer over a tabbed
 * Description / Tags / Related group on the left (~66%); the seven cards in
 * two tabbed groups (kept in today's order) on the right. */
export function buildDefaultLayout(api: DockviewApi, opts: { hasSliced?: boolean } = {}): void {
  api.clear();
  const { leftTop, leftBottom, rightTop, rightBottom } = DEFAULT_GROUPS;
  const viewer = addPanel(api, leftTop[0]);
  const descriptionGroup = addPanel(api, leftBottom[0], { referencePanel: viewer, direction: "below" });
  for (const id of leftBottom.slice(1)) addPanel(api, id, { referencePanel: descriptionGroup, direction: "within" });
  const rightA = addPanel(api, rightTop[0], { referencePanel: viewer, direction: "right" });
  for (const id of rightTop.slice(1)) addPanel(api, id, { referencePanel: rightA, direction: "within" });
  const rightB = addPanel(api, rightBottom[0], { referencePanel: rightA, direction: "below" });
  for (const id of rightBottom.slice(1)) addPanel(api, id, { referencePanel: rightB, direction: "within" });

  const active = { ...DEFAULT_ACTIVE, ...(opts.hasSliced ? { rightTop: "gcodeProfiles" as PanelId } : {}) };
  for (const group of Object.keys(active) as DefaultGroup[]) api.getPanel(active[group])?.api.setActive();

  // Sizes are best-effort: a not-yet-measured (zero-sized) container skips them.
  const { width, height } = api;
  if (width > 0 && height > 0) {
    viewer.api.setSize({ width: width * LEFT_WIDTH, height: height * LEFT_TOP_HEIGHT });
    rightA.api.setSize({ height: height * RIGHT_TOP_HEIGHT });
  }
}

/** Re-adds one closed panel near where it lives in the default layout. */
export function addPanelAtDefault(api: DockviewApi, id: PanelId): void {
  const has = (other: PanelId) => other !== id && api.getPanel(other) !== undefined;
  const ref = (other: PanelId) => api.getPanel(other)!;
  const groupOf = (Object.keys(DEFAULT_GROUPS) as DefaultGroup[]).find((g) =>
    (DEFAULT_GROUPS[g] as readonly PanelId[]).includes(id),
  )!;

  const sibling = (DEFAULT_GROUPS[groupOf] as readonly PanelId[]).find(has);
  if (sibling) return void addPanel(api, id, { referencePanel: ref(sibling), direction: "within" });

  const anyOf = (group: DefaultGroup) => (DEFAULT_GROUPS[group] as readonly PanelId[]).find(has);
  const placements: Record<DefaultGroup, Array<[DefaultGroup, Direction]>> = {
    leftTop: [["leftBottom", "above"], ["rightTop", "left"], ["rightBottom", "left"]],
    leftBottom: [["leftTop", "below"], ["rightTop", "left"], ["rightBottom", "left"]],
    rightTop: [["rightBottom", "above"], ["leftTop", "right"], ["leftBottom", "right"]],
    rightBottom: [["rightTop", "below"], ["leftTop", "right"], ["leftBottom", "right"]],
  };
  for (const [neighbour, direction] of placements[groupOf]) {
    const target = anyOf(neighbour);
    if (target) return void addPanel(api, id, { referencePanel: ref(target), direction });
  }
  addPanel(api, id);
}
