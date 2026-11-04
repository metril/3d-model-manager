import { EyeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PlatePanel } from "@/components/model-detail/PlatePanel";
import { selectablePlates } from "@/components/viewer/plates";
import type { StudioSelection } from "@/components/model-detail/studioSelection";
import type { FileOut, ModelDetail } from "@/api/types";

/** Right-column card for sliced-plate files: compact metadata + plate
 * thumbnails per file, with a one-click "View" that switches the main viewer
 * panel to the layer preview (no embedded preview here). Renders nothing when
 * the model has no sliced files. */
export function GcodeProfilesCard({
  model,
  selection,
  onView,
}: {
  model: ModelDetail;
  selection?: StudioSelection;
  onView?: (file: FileOut) => void;
}) {
  const slicedFiles = (model.current_revision?.files ?? []).filter((file) => file.kind === "sliced");
  // Unsliced multi-plate projects: their plates, selectable into the viewer.
  const plateFiles = (model.current_revision?.files ?? []).filter(
    (file) => file.kind !== "sliced" && selectablePlates(file.meta?.plates).some((p) => p.has_glb),
  );
  if (slicedFiles.length === 0 && plateFiles.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{slicedFiles.length === 0 ? "Plates" : "G-code profiles"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {plateFiles.map((file) => (
          <div key={`plates-${file.id}`} className="space-y-2">
            <span className="block truncate text-sm font-medium" title={file.rel_path}>
              {file.rel_path.split("/").pop()}
            </span>
            <PlatePanel file={file} compact />
          </div>
        ))}
        {slicedFiles.map((file) => {
          const active = selection?.type === "file" && selection.id === file.id;
          return (
            <div key={file.id} className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium" title={file.rel_path}>
                  {file.rel_path.split("/").pop()}
                </span>
                {onView && (
                  <Button
                    variant={active ? "secondary" : "outline"}
                    size="sm"
                    aria-pressed={active}
                    onClick={() => onView(file)}
                  >
                    <EyeIcon className="size-4" />
                    {active ? "Viewing" : "View"}
                  </Button>
                )}
              </div>
              <PlatePanel file={file} noPreview />
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
