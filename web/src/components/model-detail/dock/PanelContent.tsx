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
import { useRelocateEmitter } from "@/lib/windowRelocate";
import { useModelDetail } from "./ModelDetailContext";
import type { PanelId } from "./panelMeta";


// Thin adapters: the cards keep their own props; these read the page context.
function ViewerContent({ fill }: { fill?: boolean }) {
  const { model, studio } = useModelDetail();
  return (
    <StudioWorkspace
      model={model}
      glbable={studio.glbable}
      others={studio.others}
      selection={studio.selection}
      onSelectAssembly={studio.onSelectAssembly}
      onSelectFile={studio.onSelectFile}
      fill={fill}
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
  return <GcodeProfilesCard model={useModelDetail().model} selection={useModelDetail().studio.selection} onView={useModelDetail().studio.onSelectFile} />;
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

const CONTENT: Record<PanelId, ComponentType<{ fill?: boolean }>> = {
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

/** Bare panel body for `id`; also used directly by the stacked (<900px) fallback. */
export function PanelContent({ id, fill }: { id: PanelId; fill?: boolean }) {
  const Content = CONTENT[id];
  return <Content fill={fill} />;
}

/** Scrolling frame around a docked panel. Tracks which window currently hosts
 * it and publishes that window's <body> so Radix overlays opened from inside
 * the panel render in the same window (a pop-out), not behind it. */
function PanelFrame({ api, fill, children }: { api: IDockviewPanelProps["api"]; fill?: boolean; children: React.ReactNode }) {
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
    // Fires after the panel has settled in its new group/window.
    const sub = api.onDidLocationChange(sync);
    return () => sub.dispose();
  }, [api, relocated]);

  return (
    <PortalContainerContext.Provider value={container}>
      <div ref={ref} className={fill ? "h-full overflow-hidden p-2" : "h-full overflow-auto p-2"}>
        {children}
      </div>
    </PortalContainerContext.Provider>
  );
}

/** The single dockview component behind every panel; dockview passes the panel
 * id as `api.id`. */
export function DockPanel(props: IDockviewPanelProps) {
  const id = props.api.id as PanelId;
  return (
    <PanelFrame api={props.api} fill={id === "viewer"}>
      <PanelContent id={id} fill={id === "viewer"} />
    </PanelFrame>
  );
}
