import i18n from "i18next";
import type { FunctionComponent } from "react";
import type { IDockviewPanelProps } from "dockview-react";

import { DockPanel } from "./PanelContent";
import { PANEL_IDS, PANEL_TITLE_KEYS, type PanelId } from "./panelMeta";

export { PANEL_IDS, PANEL_TITLE_KEYS, type PanelId };

export type PanelDef = {
  id: PanelId;
  title: string;
  component: FunctionComponent<IDockviewPanelProps>;
};

/** Registry of dockable panels. */
export const PANEL_REGISTRY: Record<PanelId, PanelDef> = Object.fromEntries(
  PANEL_IDS.map((id) => [id, { id, title: i18n.t(PANEL_TITLE_KEYS[id]), component: DockPanel }]),
) as Record<PanelId, PanelDef>;

/** `components` map for `<DockviewReact>`; keys double as `contentComponent`. */
export const DOCK_COMPONENTS: Record<string, FunctionComponent<IDockviewPanelProps>> = Object.fromEntries(
  PANEL_IDS.map((id) => [id, PANEL_REGISTRY[id].component]),
);
