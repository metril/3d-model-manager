/** V4 projects: BOM, progress/cost rollups, timeline, templates, queue-remaining. */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type ProjectStatus = "planning" | "active" | "on_hold" | "completed" | "archived";
export const PROJECT_STATUSES: ProjectStatus[] = ["planning", "active", "on_hold", "completed", "archived"];

export interface ProjectItem {
  id: number;
  kind: "part" | "purchased";
  position: number;
  name: string | null;
  model_id: number | null;
  model_slug: string | null;
  model_name: string | null;
  file_id: number | null;
  file_name: string | null;
  plate: number | null;
  quantity: number;
  done: number;
  queued: number;
  outstanding: number;
  acquired: number;
  unit_cost: number | null;
  line_cost: number | null;
  url: string | null;
  notes: string | null;
}

export interface CostRollup {
  filament_g: number;
  print_time_s: number;
  print_cost: number;
  purchased_cost: number;
  total_cost: number;
  estimated_remaining_cost: number;
}

export interface ProjectProgress {
  parts_total: number;
  parts_done: number;
  parts_queued: number;
  purchased_total: number;
  purchased_acquired: number;
  percent: number;
}

export interface ProjectAttachment {
  id: number;
  filename: string;
  content_type: string | null;
  size: number;
  created_at: string;
}

export interface ProjectSummary {
  id: number;
  name: string;
  description: string;
  status: ProjectStatus;
  due_date: string | null;
  overdue: boolean;
  cover_model_id: number | null;
  cover_model_slug: string | null;
  tags: string[];
  is_template: boolean;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  progress: ProjectProgress;
  cost: CostRollup;
}

export interface ProjectDetail extends ProjectSummary {
  notes: string;
  items: ProjectItem[];
  attachments: ProjectAttachment[];
}

export interface ProjectArchive {
  id: number;
  model_id: number | null;
  model_name: string | null;
  plate: number | null;
  result: string;
  duration_s: number | null;
  filament_g: number | null;
  total_cost: number | null;
  explicit: boolean;
  created_at: string;
}

export interface TimelineEntry {
  at: string;
  kind: string;
  message: string;
  user_id: number | null;
}

export interface QueueRemainingResult {
  enqueued: { item_id: number; queue_id: number; copies: number }[];
  skipped: { item_id: number; model: string; reason: string }[];
}

export interface ProjectFilters {
  status?: ProjectStatus;
  q?: string;
}

export interface ProjectBody {
  name?: string;
  description?: string;
  status?: ProjectStatus;
  due_date?: string | null;
  cover_model_id?: number | null;
  tags?: string[];
  notes?: string;
}

export interface ItemBody {
  kind?: "part" | "purchased";
  name?: string | null;
  model_id?: number | null;
  file_id?: number | null;
  plate?: number | null;
  quantity?: number;
  acquired?: number;
  unit_cost?: number | null;
  url?: string | null;
  notes?: string | null;
}

export const projectsQueryOptions = (filters: ProjectFilters = {}) => {
  const p = new URLSearchParams();
  if (filters.status) p.set("status", filters.status);
  if (filters.q) p.set("q", filters.q);
  const qs = p.toString();
  return queryOptions({
    queryKey: ["projects", "list", filters] as const,
    queryFn: () => api.get<ProjectSummary[]>(`/projects${qs ? `?${qs}` : ""}`),
  });
};

export const projectTemplatesQueryOptions = queryOptions({
  queryKey: ["projects", "templates"] as const,
  queryFn: () => api.get<ProjectSummary[]>("/projects/templates"),
});

export function useProjects(filters: ProjectFilters = {}) {
  return useQuery(projectsQueryOptions(filters));
}

export function useProjectTemplates() {
  return useQuery(projectTemplatesQueryOptions);
}

export function useProject(id: number) {
  return useQuery({
    queryKey: ["projects", "detail", id] as const,
    queryFn: () => api.get<ProjectDetail>(`/projects/${id}`),
  });
}

export function useProjectArchives(id: number) {
  return useQuery({
    queryKey: ["projects", "archives", id] as const,
    queryFn: () => api.get<ProjectArchive[]>(`/projects/${id}/archives`),
  });
}

export function useProjectTimeline(id: number) {
  return useQuery({
    queryKey: ["projects", "timeline", id] as const,
    queryFn: () => api.get<TimelineEntry[]>(`/projects/${id}/timeline`),
  });
}

function useProjectMutation<V, R>(fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useCreateProject() {
  return useProjectMutation((body: ProjectBody & { name: string }) =>
    api.post<ProjectDetail>("/projects", body),
  );
}

export function usePatchProject(id: number) {
  return useProjectMutation((body: ProjectBody) => api.patch<ProjectDetail>(`/projects/${id}`, body));
}

/**
 * Drops the deleted project's detail/archives/timeline queries (cancelling any
 * in flight) BEFORE the list is invalidated, so the still-mounted detail page
 * never refetches a now-404 project while it navigates away.
 */
export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/projects/${id}`),
    onSuccess: async (_data, id) => {
      const keys = [
        ["projects", "detail", id],
        ["projects", "archives", id],
        ["projects", "timeline", id],
      ] as const;
      await Promise.all(keys.map((queryKey) => qc.cancelQueries({ queryKey })));
      keys.forEach((queryKey) => qc.removeQueries({ queryKey }));
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useAddItem(id: number) {
  return useProjectMutation((body: ItemBody & { kind: "part" | "purchased" }) =>
    api.post<ProjectItem>(`/projects/${id}/items`, body),
  );
}

export function usePatchItem(id: number) {
  return useProjectMutation(({ itemId, body }: { itemId: number; body: ItemBody }) =>
    api.patch<ProjectItem>(`/projects/${id}/items/${itemId}`, body),
  );
}

export function useDeleteItem(id: number) {
  return useProjectMutation((itemId: number) => api.delete<void>(`/projects/${id}/items/${itemId}`));
}

/** Library multi-select / model page "add to project". */
export function useAddModelsToProject() {
  return useProjectMutation(({ projectId, modelIds }: { projectId: number; modelIds: number[] }) =>
    api.post<ProjectDetail>(`/projects/${projectId}/models`, { model_ids: modelIds, quantity: 1 }),
  );
}

export function useLinkArchives(id: number) {
  return useProjectMutation(({ ids, unlink }: { ids: number[]; unlink: boolean }) =>
    api.post<Record<string, number>>(`/projects/${id}/archives${unlink ? "/remove" : ""}`, {
      archive_ids: ids,
    }),
  );
}

export function useQueueRemaining(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<QueueRemainingResult>(`/projects/${id}/queue-remaining`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

export function useCreateTemplate(id: number) {
  return useProjectMutation((name: string) =>
    api.post<ProjectDetail>(`/projects/${id}/create-template`, { name }),
  );
}

export function useFromTemplate() {
  return useProjectMutation(({ templateId, name }: { templateId: number; name: string }) =>
    api.post<ProjectDetail>(`/projects/from-template/${templateId}`, { name }),
  );
}

export function useUploadAttachment(id: number) {
  return useProjectMutation((file: File) =>
    api.postRaw<ProjectAttachment>(
      `/projects/${id}/attachments?filename=${encodeURIComponent(file.name)}`,
      file,
      file.type || "application/octet-stream",
    ),
  );
}

export function useDeleteAttachment(id: number) {
  return useProjectMutation((attId: number) =>
    api.delete<void>(`/projects/${id}/attachments/${attId}`),
  );
}

export const exportUrl = (id: number, format: "json" | "csv" | "zip") =>
  `/api/projects/${id}/export?format=${format}`;
export const attachmentUrl = (id: number, attId: number) =>
  `/api/projects/${id}/attachments/${attId}`;

export const statusLabel = (s: string) => s.replace("_", " ");
