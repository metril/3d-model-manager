import { useEffect, useRef, useState, type ComponentType } from "react";
import type { IDockviewPanelProps } from "dockview-react";

import { RelatedModels } from "@/components/model-detail/RelatedModels";
import { StudioWorkspace } from "@/components/model-detail/StudioWorkspace";
import { DescriptionCard } from "@/components/model-detail/cards/DescriptionCard";
import { FilesDocsCard } from "@/components/model-detail/cards/FilesDocsCard";
import { GcodeProfilesCard } from "@/components/model-detail/cards/GcodeProfilesCard";
import { NotesCard } from "@/components/model-detail/cards/NotesCard";
import { PrintHistoryCard } from "@/components/model-detail/cards/PrintHistoryCard";
import { PrintTipsCard } from "@/components/model-detail/cards/PrintTipsCard";
import { RevisionsCard } from "@/components/model-detail/cards/RevisionsCard";
import { SpecsCard } from "@/components/model-detail/cards/SpecsCard";
import { TagsLinksCard } from "@/components/model-detail/cards/TagsLinksCard";
import { PortalContainerContext } from "@/lib/portalContainer";
import { useModelDetail } from "./ModelDetailContext";
import { PANEL_IDS, PANEL_TITLES, type PanelId } from "./panelMeta";

export { PANEL_IDS, PANEL_TITLES, type PanelId };

// Thin adapters: the cards keep their own props; these read the page context.
function ViewerContent() {
  const { model, studio } = useModelDetail();
  return (
    <StudioWorkspace
      model={model}
      glbable={studio.glbable}
      others={studio.others}
      selection={studio.selection}
      onSelectAssembly={studio.onSelectAssembly}
    />
  );
}
function DescriptionContent() {
  const { model, editMode } = useModelDetail();
  return <DescriptionCard model={model} editMode={editMode} />;
}
function TagsLinksContent() {
  const { model, editMode, relocateOpen, setRelocateOpen } = useModelDetail();
  return (
    <TagsLinksCard
      model={model}
      editMode={editMode}
      relocateOpen={relocateOpen}
      onRelocateOpenChange={setRelocateOpen}
    />
  );
}
function RelatedContent() {
  return <RelatedModels model={useModelDetail().model} />;
}
function GcodeProfilesContent() {
  return <GcodeProfilesCard model={useModelDetail().model} />;
}
function PrintHistoryContent() {
  return <PrintHistoryCard model={useModelDetail().model} />;
}
function FilesDocsContent() {
  const { model, studio } = useModelDetail();
  return <FilesDocsCard model={model} onViewIn3D={studio.onViewIn3D} />;
}
function RevisionsContent() {
  return <RevisionsCard model={useModelDetail().model} />;
}
function NotesContent() {
  return <NotesCard model={useModelDetail().model} />;
}
function PrintTipsContent() {
  return <PrintTipsCard model={useModelDetail().model} />;
}
function SpecsContent() {
  return <SpecsCard model={useModelDetail().model} />;
}

/** Bare panel bodies, used directly by the stacked (<900px) fallback. */
export const PANEL_CONTENT: Record<PanelId, ComponentType> = {
  viewer: ViewerContent,
  description: DescriptionContent,
  tagsLinks: TagsLinksContent,
  related: RelatedContent,
  gcodeProfiles: GcodeProfilesContent,
  printHistory: PrintHistoryContent,
  filesDocs: FilesDocsContent,
  revisions: RevisionsContent,
  notes: NotesContent,
  printTips: PrintTipsContent,
  specs: SpecsContent,
};

/** Scrolling frame around a docked panel. Tracks which window currently hosts
 * it and publishes that window's <body> so Radix overlays opened from inside
 * the panel render in the same window (a pop-out), not behind it. */
function PanelFrame({ api, children }: { api: IDockviewPanelProps["api"]; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLElement | undefined>(undefined);

  useEffect(() => {
    const sync = () => {
      const body = ref.current?.ownerDocument.body;
      setContainer(body && body !== document.body ? body : undefined);
    };
    sync();
    // Fires after the panel has settled in its new group/window.
    const sub = api.onDidLocationChange(sync);
    return () => sub.dispose();
  }, [api]);

  return (
    <PortalContainerContext.Provider value={container}>
      <div ref={ref} className="h-full overflow-auto p-2">
        {children}
      </div>
    </PortalContainerContext.Provider>
  );
}

function makeDockComponent(id: PanelId): ComponentType<IDockviewPanelProps> {
  const Content = PANEL_CONTENT[id];
  function DockPanel(props: IDockviewPanelProps) {
    return (
      <PanelFrame api={props.api}>
        <Content />
      </PanelFrame>
    );
  }
  DockPanel.displayName = `DockPanel(${id})`;
  return DockPanel;
}

export type PanelDef = {
  id: PanelId;
  title: string;
  component: ComponentType<IDockviewPanelProps>;
};

export const PANEL_REGISTRY: Record<PanelId, PanelDef> = Object.fromEntries(
  PANEL_IDS.map((id) => [id, { id, title: PANEL_TITLES[id], component: makeDockComponent(id) }]),
) as Record<PanelId, PanelDef>;

/** `components` map for `<DockviewReact>`; keys double as `contentComponent`. */
export const DOCK_COMPONENTS: Record<string, ComponentType<IDockviewPanelProps>> = Object.fromEntries(
  PANEL_IDS.map((id) => [id, PANEL_REGISTRY[id].component]),
);
