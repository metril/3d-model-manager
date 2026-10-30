import type { FunctionComponent } from "react";
import type { IDockviewPanelProps } from "dockview-react";

import { DockPanel } from "./PanelContent";
import { PANEL_IDS } from "./panelMeta";

/** `components` map for `<DockviewReact>`; keys double as `contentComponent`. */
export const DOCK_COMPONENTS: Record<string, FunctionComponent<IDockviewPanelProps>> = Object.fromEntries(
  PANEL_IDS.map((id) => [id, DockPanel]),
);
