import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import type { ModelDetail } from "@/api/types";

/** Whether the model page can open the shared PrintDialog: a sliced file to
 * plan against (or a source the slicer sidecar can slice) AND the blanket
 * `printers:print` permission. Single source for ModelHeader's Print button
 * and the viewer's "Print plate N" button. */
export function useCanPrintModel(model: ModelDetail): boolean {
  const { can } = useAuth();
  const features = useFeatures();
  const revFiles = model.current_revision?.files ?? [];
  const hasSlicedFile = revFiles.some((f) => f.format === "gcode_3mf");
  const hasSliceableFile =
    !!features.data?.slicer_configured && revFiles.some((f) => ["3mf", "stl", "step", "obj"].includes(f.format));
  return (hasSlicedFile || hasSliceableFile) && can("printers:print");
}
