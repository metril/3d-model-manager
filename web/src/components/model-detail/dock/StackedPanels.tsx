import { DetailLayout } from "@/components/model-detail/DetailLayout";
import { PanelContent } from "./PanelContent";
import { LEFT_PANELS, RIGHT_PANELS } from "./panelMeta";

/** Narrow-viewport (<900px) fallback: no dock, the same panels stacked in
 * today's order (left column first). */
export function StackedPanels() {
  return (
    <DetailLayout
      left={LEFT_PANELS.map((id) => (
        <PanelContent key={id} id={id} />
      ))}
      right={RIGHT_PANELS.map((id) => (
        <PanelContent key={id} id={id} />
      ))}
    />
  );
}
