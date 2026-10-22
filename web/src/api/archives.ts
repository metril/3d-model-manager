/** Print archive hooks (backend/app/api/archives.py, P2). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ArchiveOut, ArchiveResult, PrintEntry, PrintPatchIn } from "@/api/types";

/** Poll every 5s while any run is still pulling artifacts off the printer. */
export function archivesRefetchInterval(rows: ArchiveOut[] | undefined): number | false {
  return rows?.some((r) => r.fetch_state === "pending") ? 5000 : false;
}

export interface ArchiveFilters {
  printerId?: number;
  result?: ArchiveResult;
  modelId?: number;
}

export function useArchives(filters: ArchiveFilters = {}) {
  const params = new URLSearchParams();
  if (filters.printerId != null) params.set("printer", String(filters.printerId));
  if (filters.result) params.set("result", filters.result);
  if (filters.modelId != null) params.set("model", String(filters.modelId));
  const qs = params.toString();
  return useQuery({
    queryKey: ["archives", filters.printerId ?? null, filters.result ?? null, filters.modelId ?? null] as const,
    queryFn: () => api.get<ArchiveOut[]>(`/archives${qs ? `?${qs}` : ""}`),
    refetchInterval: (q) => archivesRefetchInterval(q.state.data),
  });
}

export function useArchive(id: number | null) {
  return useQuery({
    queryKey: ["archives", "detail", id] as const,
    queryFn: () => api.get<ArchiveOut>(`/archives/${id}`),
    enabled: id != null,
    refetchInterval: (q) => (q.state.data?.fetch_state === "pending" ? 5000 : false),
  });
}

export function usePatchRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: PrintPatchIn }) =>
      api.patch<PrintEntry>(`/prints/${id}`, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["archives"] });
      void qc.invalidateQueries({ queryKey: ["prints"] });
    },
  });
}

export const archiveThumbnailUrl = (id: number): string => `/api/archives/${id}/thumbnail`;
export const archiveFileUrl = (id: number): string => `/api/archives/${id}/file`;
