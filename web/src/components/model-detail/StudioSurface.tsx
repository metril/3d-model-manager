import { ArrowLeftIcon, LoaderCircleIcon } from "lucide-react";

import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { SegmentedControl } from "@/components/viewer/SegmentedControl";
import { PlatePanel } from "@/components/model-detail/PlatePanel";
import { PlaceholderCard, ViewerStage, type ViewerStageProps } from "@/components/viewer/ViewerStage";
import type { StudioSelection } from "@/components/model-detail/studioSelection";
import type { FileOut } from "@/api/types";

type StageProps = Omit<ViewerStageProps, "variant" | "showWindowButtons">;

function FileState({ file, fill = false }: { file: FileOut; fill?: boolean }) {
  if (file.kind === "sliced") return <PlatePanel file={file} fill={fill} autoPreview />;

  if (file.format === "gcode") {
    return (
      <PlaceholderCard
        title="Plain G-code — no 3D preview"
        description="This file has no mesh geometry to render."
      />
    );
  }

  switch (file.glb_status) {
    case "pending":
      return (
        <Card className="mx-auto mt-8 max-w-md">
          <CardHeader className="items-center text-center">
            <LoaderCircleIcon className="mx-auto mb-2 size-6 animate-spin text-muted-foreground" />
            <CardTitle>Preparing preview…</CardTitle>
            {/* The app-wide SSE connection (`EventsProvider`) invalidates the
                `["models"]` query when the conversion job finishes, which
                refetches this model with the new `glb_status` — no polling
                needed here. */}
          </CardHeader>
        </Card>
      );
    case "failed":
      return (
        <PlaceholderCard
          destructive
          title="Preview failed"
          description="Couldn't generate a 3D preview for this file."
        />
      );
    case "unsupported":
    default:
      return (
        <PlaceholderCard
          title="No 3D preview"
          description="This file format doesn't support in-browser previewing."
        />
      );
  }
}

/** The studio's viewing surface (R13a re-chrome): a pure switch on the rail's
 * current selection. The assembly view reuses the exact `ViewerStage` the
 * old `MeshSection` rendered (pop-out window buttons kept). The sliced-plate
 * strip that used to render underneath it here has moved to the right
 * column's `GcodeProfilesCard` (still `PlatePanel`, just relocated) -- this
 * surface no longer needs `slicedFiles` at all. */
export function StudioSurface({
  selection,
  hasGlb,
  otherFiles,
  stageProps,
  onSelectAssembly,
  onSelectFile,
  fill = false,
}: {
  /** Stretch the viewer to its container's height (dock panel) instead of the
   * fixed inline height. */
  fill?: boolean;
  selection: StudioSelection | undefined;
  hasGlb: boolean;
  otherFiles: FileOut[];
  stageProps: StageProps;
  /** R13c "View in 3D" hand-off: returns the surface to the combined
   * assembly view. Only rendered as a chip when a single file is selected
   * AND the model actually has an assembly to go back to. */
  onSelectAssembly: () => void;
  onSelectFile?: (file: FileOut) => void;
}) {
  const sliced = otherFiles.filter((f) => f.kind === "sliced");
  const canToggle = hasGlb && sliced.length > 0 && onSelectFile !== undefined;
  const toggle = canToggle ? (
    <div className="flex shrink-0 flex-wrap items-center gap-2">
      <SegmentedControl
        label="Viewer mode"
        options={["model", "sliced"] as const}
        labels={{ model: "Model", sliced: "Sliced" }}
        value={selection?.type === "assembly" ? "model" : selection?.type === "file" && sliced.some((f) => f.id === selection.id) ? "sliced" : null}
        onChange={(next) => (next === "model" ? onSelectAssembly() : onSelectFile(sliced.find((f) => selection?.type === "file" && f.id === selection.id) ?? sliced[0]))}
      />
      {selection?.type === "file" && sliced.length > 1 && (
        <select
          aria-label="Sliced file"
          className="h-7 max-w-48 rounded-md border border-border bg-card px-1 text-xs"
          value={selection.id}
          onChange={(e) => {
            const f = sliced.find((x) => x.id === Number(e.target.value));
            if (f) onSelectFile(f);
          }}
        >
          {sliced.map((f) => (
            <option key={f.id} value={f.id}>
              {f.rel_path.split("/").pop()}
            </option>
          ))}
        </select>
      )}
    </div>
  ) : null;
  if (!selection) {
    return (
      <PlaceholderCard
        title="No previewable files"
        description="Upload a mesh, CAD, or sliced file to preview it here."
      />
    );
  }

  if (selection.type === "assembly" && hasGlb) {
    return (
      <div className={fill ? "flex h-full min-h-0 min-w-0 flex-col gap-4" : "flex min-w-0 flex-col gap-4"}>
        {toggle}
        <div className={fill ? "min-h-0 flex-1" : undefined}>
          <ViewerStage {...stageProps} variant={fill ? "window" : "inline"} showWindowButtons />
        </div>
      </div>
    );
  }

  const file = otherFiles.find((candidate) => selection.type === "file" && candidate.id === selection.id);
  if (!file) {
    return (
      <PlaceholderCard
        title="No previewable files"
        description="Upload a mesh, CAD, or sliced file to preview it here."
      />
    );
  }

  return (
    <div className={fill ? "flex h-full min-h-0 min-w-0 flex-col gap-2" : "flex min-w-0 flex-col gap-2"}>
      {toggle}
      {hasGlb && !toggle && (
        <button
          type="button"
          onClick={onSelectAssembly}
          className="inline-flex w-fit items-center gap-1 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <ArrowLeftIcon className="size-3.5" />
          Back to assembly
        </button>
      )}
      <div className={fill ? "h-full min-h-0 flex-1" : undefined}>
        <FileState file={file} fill={fill} />
      </div>
    </div>
  );
}
