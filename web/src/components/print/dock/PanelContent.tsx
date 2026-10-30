import { useEffect, useRef, useState, type ComponentType } from "react";
import type { IDockviewPanelProps } from "dockview-react";

import { PortalContainerContext } from "@/lib/portalContainer";
import { useRelocateEmitter } from "@/lib/windowRelocate";
import { PrintersPanel } from "./PrintersPanel";
import { ArchivesPanel, CameraPanel, ForecastPanel, QueuePanel, StatsPanel } from "./RailPanels";
import type { PanelId } from "./panelMeta";

const CONTENT: Record<PanelId, ComponentType> = {
  printers: PrintersPanel,
  queue: QueuePanel,
  archives: ArchivesPanel,
  camera: CameraPanel,
  stats: StatsPanel,
  forecast: ForecastPanel,
};

/** Scrolling frame around a docked panel. Publishes the window currently
 * hosting it so Radix overlays opened inside render in the same window (a
 * pop-out) rather than behind it. */
function PanelFrame({ api, children }: { api: IDockviewPanelProps["api"]; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLElement | undefined>(undefined);
  const relocated = useRelocateEmitter();

  useEffect(() => {
    const sync = () => {
      const body = ref.current?.ownerDocument.body;
      setContainer(body && body !== document.body ? body : undefined);
      relocated?.emit();
    };
    sync();
    const sub = api.onDidLocationChange(sync);
    return () => sub.dispose();
  }, [api, relocated]);

  return (
    <PortalContainerContext.Provider value={container}>
      <div ref={ref} className="h-full overflow-auto p-2">
        {children}
      </div>
    </PortalContainerContext.Provider>
  );
}

/** The single dockview component behind every panel (`api.id` is the panel id). */
export function DockPanel(props: IDockviewPanelProps) {
  const Content = CONTENT[props.api.id as PanelId];
  return (
    <PanelFrame api={props.api}>
      <Content />
    </PanelFrame>
  );
}
