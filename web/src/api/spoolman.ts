/** Spoolman connector hooks (Q9): `/api/spoolman/*`. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type SpoolmanMode = "off" | "mirror" | "authoritative";

export interface SpoolmanSettings {
  url: string | null;
  mode: SpoolmanMode;
}

export interface SpoolmanTestOut {
  ok: boolean;
  version?: string | null;
  error?: string;
}

export interface SpoolmanRemoteSpool {
  id: number;
  name: string | null;
  material: string | null;
  vendor: string | null;
  color_hex: string | null;
  remaining_weight: number | null;
  used_weight: number | null;
  location: string | null;
}

export interface SpoolmanSyncOut {
  skipped: boolean;
  pulled: number;
  errors: number;
}

const settingsKey = ["spoolman", "settings"] as const;

export function useSpoolmanSettings() {
  return useQuery({
    queryKey: settingsKey,
    queryFn: () => api.get<SpoolmanSettings>("/spoolman/settings"),
  });
}

export function useUpdateSpoolmanSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SpoolmanSettings) => api.put<SpoolmanSettings>("/spoolman/settings", body),
    onSuccess: (data) => qc.setQueryData(settingsKey, data),
  });
}

export function useSpoolmanTest() {
  return useMutation({
    mutationFn: (url: string | null) => api.post<SpoolmanTestOut>("/spoolman/test", { url }),
  });
}

export function useSpoolmanSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SpoolmanSyncOut>("/spoolman/sync"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

export function useSpoolmanRemoteSpools(enabled: boolean) {
  return useQuery({
    queryKey: ["spoolman", "spools"] as const,
    queryFn: () => api.get<SpoolmanRemoteSpool[]>("/spoolman/spools"),
    enabled,
    retry: false,
  });
}

export function useSpoolmanLink() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["spools"] });
  return {
    link: useMutation({
      mutationFn: (v: { spool_id: number; spoolman_id: number }) => api.post<void>("/spoolman/link", v),
      onSuccess: invalidate,
    }),
    unlink: useMutation({
      mutationFn: (spool_id: number) => api.post<void>("/spoolman/unlink", { spool_id }),
      onSuccess: invalidate,
    }),
  };
}
