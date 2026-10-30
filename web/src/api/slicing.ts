/**
 * Query/mutation hooks for slicer profiles, print recipes, and sidecar
 * health (backend `app.api.profiles`, P6 "slicer, profiles and dispatch"
 * plan, Task 9). Mirrors `materials.ts`'s hooks shape.
 */
import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/api/client";
import type {
  PrintRecipe,
  PrintRecipeIn,
  ProfileImportResult,
  ProfileKind,
  ProfileListOut,
  SliceJob,
  SlicerHealth,
  SlicerProfile,
} from "@/api/types";

/** Scope shared by the profile/recipe/pipeline lists (backend `scope` params). */
export interface ProfileScope {
  printerId?: number;
  nozzle?: number;
  layer?: number;
  q?: string;
  /** Include out-of-scope rows (flagged `in_scope: false`). */
  all?: boolean;
  /** Hide stock presets that have a user copy. */
  dedup?: boolean;
}

function profilesQueryKey(kind?: ProfileKind, scope?: ProfileScope, full = true) {
  return ["profiles", kind ?? "all", scope ?? null, full] as const;
}

/** `GET /profiles?kind=&full=1` envelope (items + `total`/`nozzle_source`).
 * `full=1` by default: the table and `RecipeForm` (bed types) need `data`. */
export function useProfileList(kind?: ProfileKind, scope?: ProfileScope, options?: { full?: boolean }) {
  const full = options?.full ?? true;
  return useQuery({
    queryKey: profilesQueryKey(kind, scope, full),
    queryFn: () => {
      const params = new URLSearchParams();
      if (full) params.set("full", "1");
      if (kind) params.set("kind", kind);
      if (scope?.printerId != null) params.set("printer_id", String(scope.printerId));
      if (scope?.nozzle != null) params.set("nozzle_mm", String(scope.nozzle));
      if (scope?.layer != null) params.set("layer_height_mm", String(scope.layer));
      if (scope?.q) params.set("q", scope.q);
      if (scope?.all) params.set("show_all", "1");
      if (scope?.dedup) params.set("dedup", "1");
      return api.get<ProfileListOut>(`/profiles?${params.toString()}`);
    },
    placeholderData: keepPreviousData,
  });
}

/** Items only, for callers that don't need the envelope. */
export function useProfiles(kind?: ProfileKind, scope?: ProfileScope) {
  const query = useProfileList(kind, scope);
  return { ...query, data: query.data?.items };
}

export function useProfile(id: number | null | undefined) {
  return useQuery({
    queryKey: ["profiles", "detail", id] as const,
    queryFn: () => api.get<SlicerProfile>(`/profiles/${id}`),
    enabled: id != null,
  });
}

function invalidateProfiles(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["profiles"] });
}

export function useImportProfiles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { bundle: File } | { fromSidecar: true; all?: boolean }) => {
      if ("fromSidecar" in payload) {
        // `all: false` (default) limits the sync to the configured printers' models.
        return api.post<ProfileImportResult>("/profiles/import", {
          from_sidecar: true,
          all: payload.all ?? false,
        });
      }
      const form = new FormData();
      form.set("bundle", payload.bundle);
      return api.postForm<ProfileImportResult>("/profiles/import", form);
    },
    onSuccess: () => invalidateProfiles(queryClient),
  });
}

/** Prefix of every recipes query key; mutations invalidate by this. */
const RECIPES_KEY = ["recipes"] as const;

export interface RecipeScope extends ProfileScope {
  compatibleOnly?: boolean;
}

/** A bare number is the legacy `printerId` signature: the backend annotates
 * each recipe with `compatible` / `incompatible_reason` for that printer. */
export function useRecipes(arg?: number | RecipeScope) {
  const scope: RecipeScope = typeof arg === "number" ? { printerId: arg } : (arg ?? {});
  return useQuery({
    queryKey: [...RECIPES_KEY, scope.printerId ?? null, scope.nozzle ?? null, scope.layer ?? null, scope.compatibleOnly ?? false, scope.q ?? ""] as const,
    queryFn: () => {
      const params = new URLSearchParams();
      if (scope.printerId != null) params.set("printer_id", String(scope.printerId));
      if (scope.nozzle != null) params.set("nozzle_mm", String(scope.nozzle));
      if (scope.layer != null) params.set("layer_height_mm", String(scope.layer));
      if (scope.compatibleOnly) params.set("compatible_only", "1");
      if (scope.q) params.set("q", scope.q);
      const qs = params.toString();
      return api.get<PrintRecipe[]>(qs ? `/recipes?${qs}` : "/recipes");
    },
    placeholderData: keepPreviousData,
  });
}

export function useSaveRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id?: number; payload: PrintRecipeIn }) =>
      id != null
        ? api.patch<PrintRecipe>(`/recipes/${id}`, payload)
        : api.post<PrintRecipe>("/recipes", payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: RECIPES_KEY }),
  });
}

export function useDeleteRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/recipes/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: RECIPES_KEY }),
  });
}

export const slicerHealthQueryOptions = queryOptions({
  queryKey: ["slicer", "health"] as const,
  queryFn: () => api.get<SlicerHealth>("/slicer/health"),
});

export function useSlicerHealth() {
  return useQuery(slicerHealthQueryOptions);
}

// -- standalone slice jobs (backend `app.api.slice_jobs`) --------------------

const ACTIVE_SLICE_STATES = ["queued", "running"];

/** `POST /files/{id}/slice` -- slices a file with a recipe, no queue entry. */
export function useStartSlice() {
  return useMutation({
    mutationFn: ({ fileId, recipeId, plate }: { fileId: number; recipeId: number; plate?: number | null }) =>
      api.post<SliceJob>(`/files/${fileId}/slice`, { recipe_id: recipeId, plate: plate ?? null }),
  });
}

/** `GET /slice-jobs/{id}`, polled while the job is queued/running (the SSE
 * `slice.updated` event invalidates it sooner). */
export function useSliceJob(id: string | null) {
  return useQuery({
    queryKey: ["slice-job", id] as const,
    queryFn: () => api.get<SliceJob>(`/slice-jobs/${id}`),
    enabled: id != null,
    // A 403/404 (job deleted with its file, or not ours) never recovers.
    retry: (count, err) => !(err instanceof ApiError && (err.status === 403 || err.status === 404)) && count < 2,
    refetchInterval: (query) => {
      if (query.state.status === "error") return false;
      return query.state.data && !ACTIVE_SLICE_STATES.includes(query.state.data.state) ? false : 2000;
    },
  });
}

/** `DELETE /slice-jobs/{id}` -- cancels a queued/running slice job. */
export function useCancelSliceJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<SliceJob>(`/slice-jobs/${id}`),
    onSuccess: (job) => queryClient.setQueryData(["slice-job", job.id], job),
  });
}
