/**
 * Query/mutation hooks for the gallery-import domain (M5 Task 6): kick off
 * an import from a URL, poll it until terminal, and read/set the
 * Thingiverse app token. Mirrors `settings.ts`'s `queryOptions`/mutation
 * shape and `jobs.ts`'s refetchInterval-stops-on-terminal-state idiom.
 * `useImportSearch` (Workstream B task B3) backs the Search tab's
 * browse-then-import flow against `GET /imports/search`.
 */
import {
  queryOptions,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  ImportCreate,
  ImportOut,
  ImportSite,
  ImportTokensIn,
  ImportTokensOut,
  SearchResponse,
} from "@/api/types";

const ACTIVE: ReadonlyArray<ImportOut["state"]> = ["pending", "fetching", "downloading"];

// How often the "Recent imports" card polls while something is mid-flight
// (import-health task T3). `useEvents.tsx`'s `import_from_url` branch
// already invalidates `["imports"]` on every SSE transition -- this is a
// modest belt-and-suspenders poll for the window between "the job started"
// and "the SSE connection/browser tab actually delivered that event".
const LIST_POLL_MS = 5000;

const PAGE_SIZE = 50;

export type ImportsFilter = "all" | "failed";

/** The "Recent imports" card's data source -- polls every `LIST_POLL_MS`
 * ONLY while some row is non-terminal, and stops once everything has
 * settled (mirrors `useJob`/`useImport`'s refetchInterval-stops-on-terminal-
 * state idiom). `AppShell`'s failed-imports nav badge deliberately does NOT
 * use this hook -- see `useFailedImportsCount` below -- so the poll only
 * runs while the Collections page (which mounts this) is actually open. */
export function useImportsList(filter: ImportsFilter = "all") {
  return useInfiniteQuery({
    queryKey: ["imports", "list", filter] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api.get<ImportOut[]>(
        `/imports?limit=${PAGE_SIZE}&offset=${pageParam}${filter === "failed" ? "&state=failed" : ""}`,
      ),
    getNextPageParam: (last, pages) => (last.length >= PAGE_SIZE ? pages.length * PAGE_SIZE : undefined),
    select: (d) => d.pages.flat(),
    refetchInterval: (query) => {
      const rows = query.state.data?.pages.flat() ?? [];
      return rows.some((row) => ACTIVE.includes(row.state)) ? LIST_POLL_MS : false;
    },
  });
}

/** Count of failed, not-dismissed imports for the nav badge. Its own cheap
 * `GET /imports/failed-count` (so it never depends on how many rows a list
 * page holds); lives under the `["imports"]` prefix so SSE/mutation
 * invalidation keeps it honest, with no polling. */
export function useFailedImportsCount(): number {
  const { data } = useQuery({
    queryKey: ["imports", "failed-count"] as const,
    queryFn: () => api.get<{ count: number }>("/imports/failed-count"),
  });
  return data?.count ?? 0;
}

/** Acknowledge one failed import (`POST /imports/{id}/dismiss`); idempotent. */
export function useDismissImport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<ImportOut>(`/imports/${id}/dismiss`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["imports"] }),
  });
}

/** Acknowledge every failed import at once (`POST /imports/dismiss-failed`). */
export function useDismissFailedImports() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ dismissed: number }>("/imports/dismiss-failed"),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["imports"] }),
  });
}

/** Re-enqueues a `failed` import (`POST /imports/{id}/retry`) -- 409 if the
 * row isn't currently `failed`, 404 unknown id. No local `onError` here:
 * `queryClient.ts`'s global `MutationCache.onError` already toasts
 * `ApiError.detail` for every mutation failure, which is exactly the "409 ->
 * show the API detail" UX the retry button needs. */
export function useRetryImport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<ImportOut>(`/imports/${id}/retry`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["imports"] });
      // A retry can complete fast enough that the model shows up before the
      // user would ever see another live update -- invalidate eagerly
      // rather than waiting on the next SSE `job.updated`/poll tick.
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export function useCreateImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ImportCreate) => api.post<ImportOut>("/imports", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["imports"] }),
  });
}

/** Polls a single import until it reaches a terminal state, then stops. */
export function useImport(id: number | undefined) {
  return useQuery({
    queryKey: ["imports", id] as const,
    queryFn: () => api.get<ImportOut>(`/imports/${id}`),
    enabled: id !== undefined,
    refetchInterval: (q) => (ACTIVE.includes(q.state.data?.state ?? "done") ? 1500 : false),
  });
}

export const importTokensQueryOptions = queryOptions({
  queryKey: ["settings", "import-tokens"] as const,
  queryFn: () => api.get<ImportTokensOut>("/settings/import-tokens"),
});
export function useImportTokens() {
  return useQuery(importTokensQueryOptions);
}
export function useUpdateImportTokens() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ImportTokensIn) => api.put<ImportTokensOut>("/settings/import-tokens", body),
    onSuccess: (data) => qc.setQueryData(importTokensQueryOptions.queryKey, data),
  });
}

/** Federated browse-then-import search (M8 E1's `GET /imports/search`): queries
 * the selected `sites` at once (repeat `?site=`), paginated via
 * `useInfiniteQuery` — `getNextPageParam` advances while ANY site still reports
 * `has_more`. Enabled once `q` is non-empty and at least one site is selected
 * (an empty site list would otherwise read to the backend as "all sites"). The
 * caller passes an already-debounced `q`. */
export function useImportSearch(sites: ImportSite[], q: string) {
  const query = q.trim();
  const selected = [...sites].sort();
  const siteParams = selected.map((s) => `&site=${encodeURIComponent(s)}`).join("");
  return useInfiniteQuery({
    queryKey: ["imports", "search", selected, query] as const,
    queryFn: ({ pageParam }) =>
      api.get<SearchResponse>(
        `/imports/search?q=${encodeURIComponent(query)}&page=${pageParam}${siteParams}`,
      ),
    initialPageParam: 1,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.per_site.some((s) => s.has_more) ? allPages.length + 1 : undefined,
    enabled: query.length > 0 && selected.length > 0,
  });
}
