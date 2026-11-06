/**
 * Pure helpers for picking/addressing viewable files on a model's current
 * revision (Task 8). Kept free of React/three so they're trivially unit
 * testable under plain vitest (no Canvas/WebGL involved).
 */
import type { FileOut, ModelDetail } from "@/api/types";

/** One combinable mesh part passed down to the viewer scene (`useViewerScene`
 * -> `ViewerStage` -> the lazy `ModelViewer`). Kept in this dependency-free
 * module (no React/three) rather than `ModelViewer.tsx` so consumers that
 * never render the viewer -- `ViewerStage`, `useViewerScene` -- can import
 * the type without pulling the lazy R3F chunk into the main bundle (Global
 * Constraints "BUNDLE RULE"; a `import type` is erased at compile time
 * regardless, but this keeps the type's home consistent with that rule).
 *
 * `visible` (B1 "toggle-fix core") is ALWAYS present -- every combinable
 * part is included here whether or not its checkbox is checked, so the
 * scene can keep every part mounted and merely toggle this flag instead of
 * remounting the whole canvas when a checkbox changes. */
export interface ViewerPart {
  id: number;
  url: string;
  color?: string;
  visible: boolean;
  /** Plate-layout mode: scene-space (Y-up, mm) offset of this part. */
  offset?: [number, number, number];
}

// Mirrors `PREVIEW_TRIANGLE_THRESHOLD` in `backend/app/tasks/pipeline.py` —
// keep in sync by hand (Global Constraints "types.ts" convention).
const PREVIEW_TRIANGLE_THRESHOLD = 1_500_000;

/** Cache-buster for the immutable GLB URLs; bump with the backend's `GLB_VERSION`
 * (v2 = Y-up orientation fix, v3 = 3MF filament colours,
 * v4 = pause/colour-change layer-type fix, v5 = per-extruder colour changes,
 * split faces, non-display parts dropped, `paint_color`). */
export const GLB_CACHE_V = 5;

/** `GET /api/blobs/{hash}/glb`, requesting the decimated preview LOD once
 * it's ready for meshes heavy enough to need it. */
export function glbUrl(file: FileOut): string {
  const wantsPreview =
    file.glb_preview_ready && (file.meta?.triangle_count ?? 0) > PREVIEW_TRIANGLE_THRESHOLD;
  const query = wantsPreview ? `?preview=true&v=${GLB_CACHE_V}` : `?v=${GLB_CACHE_V}`;
  return `/api/blobs/${file.blob_hash}/glb${query}`;
}

/** Whether the studio surface has SOMETHING to show for this file: anything
 * the pipeline produces (or could produce) a GLB for (`glb_status !==
 * null`), a sliced file (Task 9's plate panel), or plain `.gcode` (no 3D
 * preview, but still selectable — SPEC's "no preview" card). Shared by
 * `pickViewerFiles` below and the Files card's per-row "View in 3D" action
 * (R13c hand-off), so both agree on exactly which rows the studio can
 * reach. */
export function isStudioViewable(file: FileOut): boolean {
  return file.glb_status !== null || file.kind === "sliced" || file.format === "gcode";
}

/** Files on the model's current revision that the viewer tab can offer in
 * its file picker -- see `isStudioViewable` above. Returned in `rel_path`
 * order. */
export function pickViewerFiles(model: ModelDetail): FileOut[] {
  const files = model.current_revision?.files ?? [];
  return files.filter(isStudioViewable).sort((a, b) => a.rel_path.localeCompare(b.rel_path));
}

/** The subset of `pickViewerFiles` with a ready-to-render GLB -- the only
 * files that can be combined into one multi-part scene (Workstream A
 * "multi-part combined view"). Sliced files and plain gcode never have a
 * mesh to combine, and pending/failed/unsupported GLBs have no scene to
 * render yet. */
export function glbFiles(model: ModelDetail): FileOut[] {
  return pickViewerFiles(model).filter((file) => file.glb_status === "ok");
}
