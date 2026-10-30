import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { modelQueryOptions } from "@/api/library";
import { api } from "@/api/client";
import type { FileOut } from "@/api/types";

export interface CombineItem {
  file_id: number;
  copies: number;
}

export interface CombineRequest {
  items: CombineItem[];
  layout: "grid" | "auto";
  spacing_mm: number;
  printer_profile_id?: number | null;
  bed?: { x_mm: number; y_mm: number } | null;
  name?: string | null;
  dry_run?: boolean;
}

export interface CombinePlacement {
  file_id: number;
  x_mm: number;
  y_mm: number;
  w_mm: number;
  d_mm: number;
}

export interface CombineResult {
  fits: boolean;
  bed: { x_mm: number; y_mm: number };
  required_bed: { x_mm: number; y_mm: number };
  placements: CombinePlacement[];
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
    mutationFn: (request: CombineRequest) =>
      api.post<CombineResult>(`/models/${encodeURIComponent(slug)}/combine`, { ...request, dry_run: false }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey });
    },
  });
}
