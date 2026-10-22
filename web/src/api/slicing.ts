/**
 * Query/mutation hooks for slicer profiles, print recipes, and sidecar
 * health (backend `app.api.profiles`, P6 "slicer, profiles and dispatch"
 * plan, Task 9). Mirrors `materials.ts`'s hooks shape.
 */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  PrintRecipe,
  PrintRecipeIn,
  ProfileImportResult,
  ProfileKind,
  SlicerHealth,
  SlicerProfile,
} from "@/api/types";

function profilesQueryKey(kind?: ProfileKind) {
  return ["profiles", kind ?? "all"] as const;
}

/** `GET /profiles?kind=&full=1` -- always fetches `data` too (`full=1`),
 * since every caller (the table, and `RecipeForm`'s bed-type lookup) needs
 * it; the backend only omits `data` from a list response to keep it light
 * when nothing else wants it, which isn't the case for this UI. */
export function useProfiles(kind?: ProfileKind) {
  return useQuery({
    queryKey: profilesQueryKey(kind),
    queryFn: () => {
      const params = new URLSearchParams({ full: "1" });
      if (kind) params.set("kind", kind);
      return api.get<SlicerProfile[]>(`/profiles?${params.toString()}`);
    },
  });
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
    mutationFn: (payload: { bundle: File } | { fromSidecar: true }) => {
      if ("fromSidecar" in payload) {
        return api.post<ProfileImportResult>("/profiles/import", { from_sidecar: true });
      }
      const form = new FormData();
      form.set("bundle", payload.bundle);
      return api.postForm<ProfileImportResult>("/profiles/import", form);
    },
    onSuccess: () => invalidateProfiles(queryClient),
  });
}

export const recipesQueryOptions = queryOptions({
  queryKey: ["recipes"] as const,
  queryFn: () => api.get<PrintRecipe[]>("/recipes"),
});

export function useRecipes() {
  return useQuery(recipesQueryOptions);
}

export function useSaveRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id?: number; payload: PrintRecipeIn }) =>
      id != null
        ? api.patch<PrintRecipe>(`/recipes/${id}`, payload)
        : api.post<PrintRecipe>("/recipes", payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: recipesQueryOptions.queryKey }),
  });
}

export function useDeleteRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/recipes/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: recipesQueryOptions.queryKey }),
  });
}

export const slicerHealthQueryOptions = queryOptions({
  queryKey: ["slicer", "health"] as const,
  queryFn: () => api.get<SlicerHealth>("/slicer/health"),
});

export function useSlicerHealth() {
  return useQuery(slicerHealthQueryOptions);
}
