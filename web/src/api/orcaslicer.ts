/** OrcaSlicer sidecar: open a file in the streamed GUI, track the active session. */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/api/auth";
import { api } from "@/api/client";
import { useFeatures } from "@/api/features";
import type { FileOut } from "@/api/types";

export interface OrcaSlicerSession {
  token: string;
  model_slug: string;
  model_id: number;
  revision_id: number;
  file: { id: number; name: string };
  created_at: string;
  expires_at: string;
  orcaslicer_running: boolean;
}

export interface OpenInOrcaSlicerOut {
  token: string;
  iframe_path: string;
  replaced_session: boolean;
  file: { id: number; name: string };
}

/** Formats OrcaSlicer can open (the backend answers 400 for anything else). */
export const ORCASLICER_FORMATS = ["3mf", "stl", "obj", "step"] as const;

export function isOrcaSlicerOpenable(file: Pick<FileOut, "format">): boolean {
  return (ORCASLICER_FORMATS as readonly string[]).includes(file.format);
}

export const orcaslicerSessionQueryOptions = queryOptions({
  queryKey: ["orcaslicer", "session"] as const,
  // 204 (no session) resolves to undefined; normalise to null (react-query forbids undefined).
  queryFn: async () => (await api.get<OrcaSlicerSession | undefined>("/orcaslicer/session")) ?? null,
});

/** OrcaSlicer is configured and switched on (Settings -> Server) AND the user is an admin
 * (the proxy and session routes are admin-only, so members would see dead entries). */
export function useOrcaSlicerEnabled(): boolean {
  const configured = !!useFeatures().data?.orcaslicer_configured;
  const isAdmin = useAuth().data?.role === "admin";
  return configured && isAdmin;
}

export function useOrcaSlicerSession(opts: { enabled?: boolean } = {}) {
  return useQuery({
    ...orcaslicerSessionQueryOptions,
    enabled: opts.enabled ?? true,
  });
}

export function useOpenInOrcaSlicer(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fileId: number) => api.post<OpenInOrcaSlicerOut>(`/models/${slug}/files/${fileId}/orcaslicer`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: orcaslicerSessionQueryOptions.queryKey }),
  });
}

export function useEndOrcaSlicerSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>("/orcaslicer/session"),
    onSuccess: () => qc.invalidateQueries({ queryKey: orcaslicerSessionQueryOptions.queryKey }),
  });
}
