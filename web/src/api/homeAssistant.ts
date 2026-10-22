/**
 * Query/mutation hooks for the Home Assistant settings API (P8 Task 8):
 * connection config (`GET`/`PUT /settings/home-assistant`, secrets
 * write-only), the two connection tests (unsaved overrides for MQTT, the
 * stored config for REST), and `ha_links` CRUD. Mirrors `appSettings.ts`/
 * `apiTokens.ts`'s `queryOptions`/mutation shape.
 */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { HaLink, HaLinkIn, HaLinkPatchIn, HaSettings, HaSettingsIn, HaTestOut } from "@/api/types";

export const haSettingsQueryOptions = queryOptions({
  queryKey: ["settings", "home-assistant"] as const,
  queryFn: () => api.get<HaSettings>("/settings/home-assistant"),
});

export const haLinksQueryOptions = queryOptions({
  queryKey: ["settings", "home-assistant", "links"] as const,
  queryFn: () => api.get<HaLink[]>("/settings/home-assistant/links"),
});

export function useHaSettings() {
  return useQuery(haSettingsQueryOptions);
}

export function useSaveHaSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: HaSettingsIn) => api.put<HaSettings>("/settings/home-assistant", body),
    onSuccess: (data) => qc.setQueryData(haSettingsQueryOptions.queryKey, data),
  });
}

/** `POST /settings/home-assistant/test-mqtt` -- `overrides` tries unsaved
 * edits before they're PUT; omit to test the stored config as-is. */
export function useTestHaMqtt() {
  return useMutation({
    mutationFn: (overrides?: HaSettingsIn) =>
      api.post<HaTestOut>("/settings/home-assistant/test-mqtt", overrides ?? {}),
  });
}

export function useTestHaRest() {
  return useMutation({
    mutationFn: () => api.post<HaTestOut>("/settings/home-assistant/test-rest", {}),
  });
}

export function useHaLinks() {
  return useQuery(haLinksQueryOptions);
}

function invalidateLinks(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: haLinksQueryOptions.queryKey });
}

/** Create (no `id`) or patch (`id` set) a link -- one hook for the dialog's
 * single submit path, mirroring `StorageBackendsCard`'s create/update
 * pairing but collapsed since both share the same body shape. */
export function useSaveHaLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: HaLinkIn | HaLinkPatchIn }) =>
      id != null
        ? api.patch<HaLink>(`/settings/home-assistant/links/${id}`, body)
        : api.post<HaLink>("/settings/home-assistant/links", body),
    onSuccess: () => invalidateLinks(qc),
  });
}

export function useDeleteHaLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/settings/home-assistant/links/${id}`),
    onSuccess: () => invalidateLinks(qc),
  });
}
