/** Print archive hooks (backend/app/api/archives.py, P2). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  ArchiveOut,
  ArchivePatchIn,
  ArchivePhoto,
  ArchivePurgeSettings,
  ArchiveResult,
  ArchiveVerdict,
  CalendarDay,
  CompareOut,
  ConfirmSettings,
  PrintEntry,
  PrintPatchIn,
  PurgePreview,
  PurgeRunOut,
  PurgeStats,
  TimelapseInfo,
  TimelapseJobState,
  TimelapseProcessIn,
} from "@/api/types";

/** Poll every 5s while any run is still pulling artifacts off the printer. */
export function archivesRefetchInterval(rows: ArchiveOut[] | undefined): number | false {
  return rows?.some((r) => r.fetch_state === "pending") ? 5000 : false;
}

export interface ArchiveFilters {
  printerId?: number;
  result?: ArchiveResult;
  modelId?: number;
  limit?: number;
}

export function useArchives(filters: ArchiveFilters = {}) {
  const params = new URLSearchParams();
  if (filters.printerId != null) params.set("printer", String(filters.printerId));
  if (filters.result) params.set("result", filters.result);
  if (filters.modelId != null) params.set("model", String(filters.modelId));
  if (filters.limit != null) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return useQuery({
    queryKey: ["archives", filters.printerId ?? null, filters.result ?? null, filters.modelId ?? null, filters.limit ?? null] as const,
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
      // P3 Task 3: the overview's `last_runs`/`recent_runs` embed ArchiveOut
      // rows -- a quality/notes patch should show up there too.
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
  });
}

export const archiveThumbnailUrl = (id: number): string => `/api/archives/${id}/thumbnail`;
export const archiveFileUrl = (id: number): string => `/api/archives/${id}/file`;
// P4 camera plan Task 9: the run's own captured snapshot/timelapse, as
// distinct from `archiveThumbnailUrl` (the sliced-plate thumbnail).
export const archiveSnapshotUrl = (id: number): string => `/api/archives/${id}/snapshot`;
export const archiveTimelapseUrl = (id: number): string => `/api/archives/${id}/timelapse`;

// ---- T7 archive extras (backend/app/api/archives_extras.py) ----

export const archivePhotoUrl = (id: number, filename: string): string =>
  `/api/archives/${id}/photos/${encodeURIComponent(filename)}`;

export function usePatchArchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: ArchivePatchIn }) =>
      api.patch<ArchiveOut>(`/archives/${id}`, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["archives"] });
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
  });
}

export function useDeleteArchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/archives/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["archives"] });
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
  });
}

export function useArchiveCompare(ids: number[], enabled = true) {
  return useQuery({
    queryKey: ["archives", "compare", ids.join(",")] as const,
    queryFn: () => api.get<CompareOut>(`/archives/compare?ids=${ids.join(",")}`),
    enabled: enabled && ids.length >= 2,
  });
}

export function useArchiveCalendar(from: string, to: string) {
  return useQuery({
    queryKey: ["archives", "calendar", from, to] as const,
    queryFn: () => api.get<CalendarDay[]>(`/archives/calendar?from=${from}&to=${to}`),
  });
}

export function useArchiveVerdict() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, verdict, source }: { id: number; verdict: ArchiveVerdict; source: "dialog" | "card" | "api" }) =>
      api.post<ArchiveOut>(`/archives/${id}/verdict`, { verdict, source }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["archives"] }),
  });
}

// -- timelapse editor --
export function useTimelapseInfo(id: number, enabled = true) {
  return useQuery({
    queryKey: ["archives", "timelapse", id, "info"] as const,
    queryFn: () => api.get<TimelapseInfo>(`/archives/${id}/timelapse/info`),
    enabled,
  });
}

export function useTimelapseThumbnails(id: number, n = 12, enabled = true) {
  return useQuery({
    queryKey: ["archives", "timelapse", id, "thumbs", n] as const,
    queryFn: () => api.get<{ frames: string[] }>(`/archives/${id}/timelapse/thumbnails?n=${n}`),
    enabled,
    staleTime: 60_000,
  });
}

export function useProcessTimelapse(id: number) {
  return useMutation({
    mutationFn: (body: TimelapseProcessIn) => {
      const fd = new FormData();
      fd.set("trim_start", String(body.trim_start));
      if (body.trim_end != null) fd.set("trim_end", String(body.trim_end));
      fd.set("speed", String(body.speed));
      fd.set("save_mode", body.save_mode);
      return api.postForm<{ job_id: string }>(`/archives/${id}/timelapse/process`, fd);
    },
  });
}

/** Polls every 1.5s until the job leaves pending/running. */
export function useTimelapseJob(id: number, jobId: string | null) {
  return useQuery({
    queryKey: ["archives", "timelapse", id, "job", jobId] as const,
    queryFn: () => api.get<TimelapseJobState>(`/archives/${id}/timelapse/jobs/${jobId}`),
    enabled: jobId != null,
    refetchInterval: (q) => (q.state.data?.state === "done" || q.state.data?.state === "error" ? false : 1500),
  });
}

// -- photos --
export function usePhotos(id: number, enabled = true) {
  return useQuery({
    queryKey: ["archives", "photos", id] as const,
    queryFn: () => api.get<ArchivePhoto[]>(`/archives/${id}/photos`),
    enabled,
  });
}

export function useUploadPhoto(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.set("file", file);
      return api.postForm<ArchivePhoto>(`/archives/${id}/photos`, fd);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["archives"] }),
  });
}

export function useDeletePhoto(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (filename: string) => api.delete<void>(`/archives/${id}/photos/${encodeURIComponent(filename)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["archives"] }),
  });
}

// -- purge + confirm settings --
export function useArchivePurgeSettings() {
  return useQuery({
    queryKey: ["settings", "archive_purge"] as const,
    queryFn: () => api.get<ArchivePurgeSettings>("/settings/archive_purge"),
  });
}

export function useUpdateArchivePurgeSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ArchivePurgeSettings) => api.put<ArchivePurgeSettings>("/settings/archive_purge", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "archive_purge"] }),
  });
}

export function usePurgeStats() {
  return useQuery({
    queryKey: ["archives", "purge", "stats"] as const,
    queryFn: () => api.get<PurgeStats>("/archives/purge/stats"),
  });
}

export function usePurgePreview() {
  return useMutation({ mutationFn: () => api.post<PurgePreview>("/archives/purge/preview") });
}

export function useRunPurge() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<PurgeRunOut>("/archives/purge/run"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["archives"] }),
  });
}

export function useConfirmSettings() {
  return useQuery({
    queryKey: ["settings", "confirm"] as const,
    queryFn: () => api.get<ConfirmSettings>("/settings/confirm"),
  });
}

export function useUpdateConfirmSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfirmSettings) => api.put<ConfirmSettings>("/settings/confirm", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "confirm"] }),
  });
}
