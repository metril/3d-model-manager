import type { FileOut } from "@/api/types";

const SLICEABLE = ["3mf", "stl", "step", "obj", "iges"];

/** A verified stored file in a format the slicer sidecar accepts. */
export function isSliceable(file: Pick<FileOut, "format" | "verified_at">): boolean {
  return !!file.verified_at && SLICEABLE.includes(file.format);
}

/** The file the model header's Slice button targets: first by format priority. */
export function pickSliceableFile<T extends Pick<FileOut, "format" | "verified_at">>(files: T[]): T | null {
  for (const fmt of SLICEABLE) {
    const f = files.find((x) => x.format === fmt && isSliceable(x));
    if (f) return f;
  }
  return null;
}
