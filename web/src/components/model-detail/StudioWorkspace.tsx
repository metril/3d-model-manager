import { useViewerScene } from "@/components/viewer/useViewerScene";
import { PlaceholderCard } from "@/components/viewer/ViewerStage";
import type { StudioSelection } from "@/components/model-detail/studioSelection";
import { StudioSurface } from "@/components/model-detail/StudioSurface";
import type { FileOut, ModelDetail } from "@/api/types";

/** Owns `useViewerScene` (called exactly once, here) for one fixed
 * glb-id-set "generation" of the model -- see `StudioWorkspace` below for
 * why it's split out like this. The rail's current selection now lives in
 * `ModelDetailPage` (via `useStudioSelection`, R13c "View in 3D" hand-off)
 * so a Files-card row action can reach it too -- this component just
 * receives it as a prop. */
function StudioWorkspaceGeneration({
  model,
  glbable,
  others,
  selection,
  onSelectAssembly,
  fill,
}: {
  model: ModelDetail;
  glbable: FileOut[];
  others: FileOut[];
  selection: StudioSelection | undefined;
  onSelectAssembly: () => void;
  /** Fill the host's height (dock panel) rather than the fixed inline stage. */
  fill?: boolean;
}) {
  const coverUrl = model.cover_blob_hash ? `/api/blobs/${model.cover_blob_hash}/thumb?size=1024` : null;
  const { stageProps } = useViewerScene({
    slug: model.slug,
    files: glbable,
    coverUrl,
    defaultAllChecked: true,
  });

  if (glbable.length === 0 && others.length === 0) {
    return (
      <PlaceholderCard
        title="No previewable files"
        description="Upload a mesh, CAD, or sliced file to preview it here."
      />
    );
  }

  return (
    <div className={fill ? "flex h-full min-h-0 min-w-0 flex-col gap-4" : "flex min-w-0 flex-col gap-4"}>
      <StudioSurface
        fill={fill}
        selection={selection}
        hasGlb={glbable.length > 0}
        otherFiles={others}
        stageProps={stageProps}
        onSelectAssembly={onSelectAssembly}
      />
    </div>
  );
}

/** Left rail + right viewing surface for the model-detail studio (Phase 4),
 * replacing the old `ViewerTab`. `useViewerScene` is owned by
 * `StudioWorkspaceGeneration` below, called exactly once for a given set of
 * ready-GLB parts -- switching the selection only changes state in
 * `ModelDetailPage`'s `useStudioSelection`, it never remounts (and so never
 * re-fetches/re-decodes) the canvas.
 *
 * Keyed on the ready-GLB id set (same trick `ViewerTab`'s `MeshSection` used)
 * because TanStack Router reuses this component instance across `$slug`
 * navigations: without the key, `useViewerScene`'s internal `checkedIds`
 * state (seeded once from `glbable[0]`) would carry model A's defaults over
 * to model B. Keying here forces a fresh mount -- and a fresh default
 * selection -- whenever the combinable file set actually changes. */
export function StudioWorkspace({
  model,
  glbable,
  others,
  selection,
  onSelectAssembly,
  fill,
}: {
  model: ModelDetail;
  glbable: FileOut[];
  others: FileOut[];
  selection: StudioSelection | undefined;
  onSelectAssembly: () => void;
  /** Fill the host's height (dock panel) rather than the fixed inline stage. */
  fill?: boolean;
}) {
  return (
    <StudioWorkspaceGeneration
      key={glbable.map((file) => file.id).join(",")}
      model={model}
      glbable={glbable}
      others={others}
      selection={selection}
      onSelectAssembly={onSelectAssembly}
      fill={fill}
    />
  );
}
