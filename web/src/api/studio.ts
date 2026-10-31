/** OrcaSlicer sidecar: open a file in the streamed GUI, track the active session. */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/api/auth";
import { api } from "@/api/client";
import { useFeatures } from "@/api/features";
import type { FileOut } from "@/api/types";

export interface StudioSession {
  token: string;
  model_slug: string;
  model_id: number;
  revision_id: number;
  file: { id: number; name: string };
  created_at: string;
  expires_at: string;
  studio_running: boolean;
}

export interface OpenInStudioOut {
  token: string;
  iframe_path: string;
  replaced_session: boolean;
  file: { id: number; name: string };
}

/** Formats Studio can open (the backend answers 400 for anything else). */
export const STUDIO_FORMATS = ["3mf", "stl", "obj", "step"] as const;

export function isStudioOpenable(file: Pick<FileOut, "format">): boolean {
  return (STUDIO_FORMATS as readonly string[]).includes(file.format);
}

export const studioSessionQueryOptions = queryOptions({
  queryKey: ["studio", "session"] as const,
  // 204 (no session) resolves to undefined; normalise to null (react-query forbids undefined).
  queryFn: async () => (await api.get<StudioSession | undefined>("/studio/session")) ?? null,
});

/** Studio is configured and switched on (Settings -> Server) AND the user is an admin
 * (the proxy and session routes are admin-only, so members would see dead entries). */
export function useStudioEnabled(): boolean {
  const configured = !!useFeatures().data?.studio_configured;
  const isAdmin = useAuth().data?.role === "admin";
  return configured && isAdmin;
}

export function useStudioSession(opts: { enabled?: boolean } = {}) {
  return useQuery({ ...studioSessionQueryOptions, enabled: opts.enabled ?? true });
}

export function useOpenInStudio(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fileId: number) => api.post<OpenInStudioOut>(`/models/${slug}/files/${fileId}/studio`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: studioSessionQueryOptions.queryKey }),
  });
}

export function useEndStudioSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>("/studio/session"),
    onSuccess: () => qc.invalidateQueries({ queryKey: studioSessionQueryOptions.queryKey }),
  });
}
