import type { FileOut } from "@/api/types";

/** Files the plate editor can show: a mesh format with a ready GLB derivative. */
export function canArrange(file: Pick<FileOut, "format" | "verified_at" | "glb_status">): boolean {
  return (
    file.verified_at !== null &&
    file.glb_status === "ok" &&
    (file.format === "stl" || file.format === "obj" || file.format === "3mf")
  );
}
