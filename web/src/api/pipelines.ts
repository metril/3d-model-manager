/**
 * Query/mutation hooks for slicer pipelines (backend `app.api.pipelines`):
 * a recipe plus ordered steps (orient/arrange pre-steps, then a rename or
 * queue post-step) run in one click on a file.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { QueueEntry, SliceJob } from "@/api/types";

export type PipelineStep =
  | { kind: "orient" }
  | { kind: "arrange" }
  | { kind: "rename"; template: string }
  | { kind: "queue"; printer_id: number | null; copies: number; front: boolean; manual_start: boolean };

export interface SlicerPipeline {
  id: number;
  name: string;
  description: string | null;
  recipe_id: number;
  recipe_name: string | null;
  plate: number | null;
  steps: PipelineStep[];
  created_by_user_id: number | null;
  created_at: string;
}

export interface SlicerPipelineIn {
  name: string;
  description: string | null;
  recipe_id: number;
  plate: number | null;
  steps: PipelineStep[];
}

export interface PipelineRun {
  kind: "slice" | "queue";
  slice_job: SliceJob | null;
  queue_entry: QueueEntry | null;
}

const KEY = ["slicer-pipelines"] as const;

export interface PipelineScope {
  printerId?: number;
  nozzle?: number;
  q?: string;
}

export function usePipelines(scope?: PipelineScope) {
  return useQuery({
    queryKey: [...KEY, scope?.printerId ?? null, scope?.nozzle ?? null, scope?.q ?? ""] as const,
    queryFn: () => {
      const params = new URLSearchParams();
      if (scope?.printerId != null) params.set("printer_id", String(scope.printerId));
      if (scope?.nozzle != null) params.set("nozzle_mm", String(scope.nozzle));
      if (scope?.q) params.set("q", scope.q);
      const qs = params.toString();
      return api.get<SlicerPipeline[]>(qs ? `/slicer-pipelines?${qs}` : "/slicer-pipelines");
    },
    placeholderData: keepPreviousData,
  });
}

export function useSavePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id?: number; payload: SlicerPipelineIn }) =>
      id != null
        ? api.patch<SlicerPipeline>(`/slicer-pipelines/${id}`, payload)
        : api.post<SlicerPipeline>("/slicer-pipelines", payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeletePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/slicer-pipelines/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** `POST /slicer-pipelines/{id}/run` -- a `slice` run returns the job to track
 * (same toast as a plain slice); a `queue` run returns the queue entry. */
export function useRunPipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, fileId, plate }: { id: number; fileId: number; plate?: number | null }) =>
      api.post<PipelineRun>(`/slicer-pipelines/${id}/run`, { file_id: fileId, plate: plate ?? null }),
    onSuccess: (run) => {
      if (run.kind === "queue") void qc.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

export function usePipelineRuns(id: number | null) {
  return useQuery({
    queryKey: [...KEY, id, "runs"] as const,
    queryFn: () => api.get<SliceJob[]>(`/slicer-pipelines/${id}/runs`),
    enabled: id != null,
  });
}
