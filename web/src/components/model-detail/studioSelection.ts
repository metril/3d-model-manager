import { useCallback, useMemo, useState } from "react";

import { glbFiles, pickViewerFiles } from "@/components/viewer/viewable";
import type { FileOut, ModelDetail } from "@/api/types";

/** What the studio surface's selection points at -- the synthetic combined-
 * assembly entry, or one non-combinable file (sliced/gcode/pending/failed/
 * unsupported). Shared with `StudioWorkspace`/`StudioSurface` so all three
 * agree on the shape without a circular import. */
export type StudioSelection = { type: "assembly" } | { type: "file"; id: number };

export function isSameSelection(a: StudioSelection | undefined, b: StudioSelection): boolean {
  if (!a) return false;
  if (a.type === "assembly") return b.type === "assembly";
  return b.type === "file" && a.id === b.id;
}

function defaultSelection(glbable: FileOut[], others: FileOut[]): StudioSelection | undefined {
  if (glbable.length > 0) return { type: "assembly" };
  if (others[0]) return { type: "file", id: others[0].id };
  return undefined;
}

/** Owns the studio's "what's showing in the viewing surface" selection,
 * lifted up to `ModelDetailPage` (R13c "View in 3D" hand-off) so a
 * Files-card row action and the studio surface can share one piece of state
 * without threading a setter down through unrelated siblings. `glbable`/
 * `others` are derived here from the same `viewable.ts` helpers
 * `StudioWorkspace` always used, so every caller agrees on one file split. */
export function useStudioSelection(model: ModelDetail | undefined): {
  selection: StudioSelection | undefined;
  glbable: FileOut[];
  others: FileOut[];
  onSelectAssembly: () => void;
  /** Switches the surface to one specific non-assembly file (sliced preview). */
  onSelectFile: (file: FileOut) => void;
  /** Files-card row action (eye icon): jumps the studio surface to this
   * file. A ready-GLB file lives in the combined assembly view rather than
   * as its own "file" entry, so route to "assembly" for those and to the
   * file's own state card otherwise. */
  onViewIn3D: (file: FileOut) => void;
} {
  const glbable = useMemo(() => (model ? glbFiles(model) : []), [model]);
  const others = useMemo(
    () => (model ? pickViewerFiles(model).filter((file) => !glbable.some((glb) => glb.id === file.id)) : []),
    [model, glbable],
  );
  const [raw, setRaw] = useState<StudioSelection | undefined>(() => defaultSelection(glbable, others));

  // A selection that no longer resolves against the CURRENT file set (a
  // "file" pointing at an id that's gone, or "assembly" when no glb parts
  // exist any more -- e.g. navigating to a different model, or a pending
  // conversion finishing and promoting a file into the assembly) falls back
  // to the same default a fresh mount would have seeded, instead of a blank
  // surface.
  const resolved: StudioSelection | undefined =
    raw && (raw.type === "assembly" ? glbable.length > 0 : others.some((file) => file.id === raw.id))
      ? raw
      : defaultSelection(glbable, others);
  // Primitive key keeps `selection` identity-stable across renders.
  const selKey = resolved ? (resolved.type === "assembly" ? "assembly" : `file:${resolved.id}`) : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const selection = useMemo(() => resolved, [selKey]);
  const onSelectAssembly = useCallback(() => setRaw({ type: "assembly" }), []);
  const onSelectFile = useCallback((file: FileOut) => setRaw({ type: "file", id: file.id }), []);
  const onViewIn3D = useCallback(
    (file: FileOut) => setRaw(file.glb_status === "ok" ? { type: "assembly" } : { type: "file", id: file.id }),
    [],
  );

  return useMemo(
    () => ({ selection, glbable, others, onSelectAssembly, onSelectFile, onViewIn3D }),
    [selection, glbable, others, onSelectAssembly, onSelectFile, onViewIn3D],
  );
}
