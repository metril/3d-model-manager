import { useMemo } from "react";
import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { modelQueryOptions } from "@/api/library";
import { api } from "@/api/client";
import type { FileOut } from "@/api/types";

export interface CombineItem {
  file_id: number;
  /** build item of a multi-object 3MF; absent = the whole file. */
  object_index?: number | null;
  copies: number;
  /** manual layout only: footprint centre on the bed, mm. */
  x_mm?: number;
  y_mm?: number;
  rot_z_deg?: number;
  scale?: number;
  copy_index?: number;
  /** unit quaternion [x, y, z, w] applied to the mesh before it is dropped on the bed. */
  orient?: [number, number, number, number];
}

export interface CombineRequest {
  items: CombineItem[];
  layout: "grid" | "auto" | "manual";
  spacing_mm: number;
  printer_profile_id?: number | null;
  bed?: { x_mm: number; y_mm: number } | null;
  name?: string | null;
  dry_run?: boolean;
  /** grid/auto: orient every item for printing first. */
  auto_orient?: boolean;
}

export interface CombinePlacement {
  file_id: number;
  x_mm: number;
  y_mm: number;
  w_mm: number;
  d_mm: number;
}

export interface CombinePose {
  file_id: number;
  object_index?: number | null;
  x_mm: number;
  y_mm: number;
  rot_z_deg: number;
  scale: number;
  copy_index: number;
  fits: boolean;
  orient: [number, number, number, number];
  /** oriented, unscaled source dims of this pose's mesh */
  w_mm: number;
  d_mm: number;
  h_mm: number;
}

/** Source mesh AABB in mm; the pose pivot is (min_x + w/2, min_y + d/2, min_z). */
export interface CombinePart {
  file_id: number;
  object_index?: number | null;
  min_x: number;
  min_y: number;
  min_z: number;
  w_mm: number;
  d_mm: number;
  h_mm: number;
}

export interface CombineResult {
  fits: boolean;
  bed: { x_mm: number; y_mm: number };
  required_bed: { x_mm: number; y_mm: number };
  placements: CombinePlacement[];
  poses: CombinePose[];
  parts: CombinePart[];
  file: FileOut | null;
}

/** Bed (x, y) mm from a printer profile's `printable_area`, or null. */
export function bedFromProfile(data: Record<string, unknown> | null | undefined): { x_mm: number; y_mm: number } | null {
  const area = data?.printable_area ?? data?.bed_shape;
  if (!Array.isArray(area) || area.length === 0) return null;
  const xs: number[] = [];
  const ys: number[] = [];
  for (const pt of area) {
    const m = /^\s*(-?[\d.]+)\s*[x,]\s*(-?[\d.]+)\s*$/.exec(String(pt));
    if (!m) return null;
    xs.push(Number(m[1]));
    ys.push(Number(m[2]));
  }
  const x = Math.max(...xs) - Math.min(...xs);
  const y = Math.max(...ys) - Math.min(...ys);
  return x > 0 && y > 0 ? { x_mm: x, y_mm: y } : null;
}

/** `POST /models/{slug}/combine` with `dry_run`: the plan only, for the fit preview. */
export function useCombinePlan(slug: string, request: CombineRequest | null) {
  return useQuery({
    queryKey: ["combine-plan", slug, request] as const,
    queryFn: () => api.post<CombineResult>(`/models/${encodeURIComponent(slug)}/combine`, { ...request, dry_run: true }),
    enabled: request !== null,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useCombine(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { silentError: true },
    mutationFn: (request: CombineRequest) =>
      api.post<CombineResult>(`/models/${encodeURIComponent(slug)}/combine`, { ...request, dry_run: false }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey });
    },
  });
}

/** `?auto=1`: the server's grid/auto layout as poses (plus each file's AABB), nothing written. */
export function useAutoArrange(slug: string) {
  return useMutation({
    meta: { silentError: true },
    mutationFn: (request: CombineRequest) =>
      api.post<CombineResult>(`/models/${encodeURIComponent(slug)}/combine?auto=1`, request),
  });
}

export interface OrientResult {
  file_id: number;
  object_index?: number | null;
  orient: [number, number, number, number];
  w_mm: number;
  d_mm: number;
  h_mm: number;
}

/** Best print orientation (quaternion + resulting dims) per file. */
export function useAutoOrient(slug: string) {
  return useMutation({
    meta: { silentError: true },
    mutationFn: (targets: { file_id: number; object_index: number | null }[]) =>
      api.post<{ files: OrientResult[] }>(`/models/${encodeURIComponent(slug)}/orient`, { targets }),
  });
}

/** One build item of a multi-object 3MF on a single bed (bed-local mm, x/y = footprint centre). */
export interface FileObject {
  index: number;
  plate: number | null;
  name: string;
  x_mm: number;
  y_mm: number;
  w_mm: number;
  d_mm: number;
  h_mm: number;
  glb_url: string;
}

/** `GET /models/{slug}/files/{id}/objects` for each 3MF: empty `objects` = use the whole file. */
export function useFileObjects(slug: string, files: Pick<FileOut, "id" | "format" | "blob_hash">[], enabled: boolean) {
  const threeMf = files.filter((f) => f.format === "3mf");
  const queries = useQueries({
    queries: threeMf.map((f) => ({
      queryKey: ["file-objects", slug, f.id, f.blob_hash] as const,
      queryFn: () =>
        api.get<{ file_id: number; objects: FileObject[] }>(
          `/models/${encodeURIComponent(slug)}/files/${f.id}/objects`,
        ),
      enabled,
      staleTime: Infinity,
      retry: false,
    })),
  });
  // A failed/unreadable 3MF falls back to the whole file.
  const settled = queries.every((q) => !enabled || q.isSuccess || q.isError);
  const stamp = queries.map((q) => `${q.dataUpdatedAt}`).join(",");
  const byFile = useMemo(() => {
    const m = new Map<number, FileObject[]>();
    queries.forEach((q, i) => {
      if (q.data && q.data.objects.length > 1) m.set(threeMf[i].id, q.data.objects);
    });
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp, threeMf.map((f) => f.id).join(",")]);
  return { byFile, settled: enabled && settled };
}
