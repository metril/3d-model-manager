/**
 * Scoped API keys (`/settings/api-keys`) for the `/api/ext/v2` remote-control
 * surface. Mint returns the plaintext key exactly once; the list shape never
 * carries it.
 */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type ApiKeyScope = "read" | "control" | "queue" | "upload";

export interface ApiKeyCreateIn {
  label: string;
  scopes: ApiKeyScope[];
  printer_ids?: number[] | null;
  expires_in_days?: number | null;
  rate_limit_per_min?: number | null;
}

export interface ApiKeyOut {
  id: number;
  label: string;
  key_prefix: string | null;
  scopes: ApiKeyScope[];
  printer_ids: number[] | null;
  expires_at: string | null;
  rate_limit_per_min: number;
  created_at: string;
  last_used_at: string | null;
  user_id: number;
  username: string;
}

export interface ApiKeyMintOut extends ApiKeyOut {
  key: string;
}

export const apiKeysQueryOptions = queryOptions({
  queryKey: ["settings", "api-keys"] as const,
  queryFn: () => api.get<ApiKeyOut[]>("/settings/api-keys"),
});

export function useApiKeys() {
  return useQuery(apiKeysQueryOptions);
}

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ApiKeyCreateIn) => api.post<ApiKeyMintOut>("/settings/api-keys", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: apiKeysQueryOptions.queryKey }),
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>("/settings/api-keys/" + id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: apiKeysQueryOptions.queryKey }),
  });
}
