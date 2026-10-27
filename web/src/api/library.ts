/**
 * Query/mutation hooks for the library domain (models, revisions, tags,
 * notes, files) — Task 8.
 */
import {
  queryOptions,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";

import { api } from "@/api/client";
import { appendTagParams, type TagMode } from "@/api/tags";
import type {
  DiffResponse,
  GalleryPage,
  JobOut,
  ModelBulkDeleteOut,
  ModelBulkIn,
  ModelBulkOut,
  ModelCreate,
  ModelDetail,
  ModelPatch,
  ModelRedownloadIn,
  ModelRelocateIn,
  ModelSummary,
  NoteCreate,
  NoteOut,
  RevisionCreate,
  RevisionDetail,
  RevisionSummary,
} from "@/api/types";

/** Prefix shared by every `useModelsQuery` cache entry (the full key also
 * carries the active `GalleryFilters`) -- R9-B's optimistic mutations match
 * against this prefix so a favorite/rename/archive/bulk edit updates every
 * cached filter variant of the list, not just the one currently mounted. */
const LIST_QUERY_KEY = ["models", "list"] as const;

type ListPages = InfiniteData<GalleryPage>;
type ListSnapshot = Array<[QueryKey, ListPages | undefined]>;

/** Maps `updater` over every cached page of every list query matching
 * `listKeyPrefix`, replacing/removing the items it flags and leaving
 * everything else -- including pages/queries `updater` never touches --
 * referentially identical (structural sharing, so unaffected cards don't
 * re-render). `updater` returns: a replacement `ModelSummary` to patch that
 * item in place, `null` to remove it (archive/delete), or `undefined` to
 * leave it untouched. Returns a snapshot of the pre-patch cache state for
 * `onError` to restore. Pure aside from the `queryClient` cache writes, and
 * shared by every mutation below so they can't drift on the merge logic. */
export function patchModelInListCache(
  queryClient: QueryClient,
  listKeyPrefix: QueryKey,
  updater: (model: ModelSummary) => ModelSummary | null | undefined,
): ListSnapshot {
  const snapshot = queryClient.getQueriesData<ListPages>({ queryKey: listKeyPrefix });
  for (const [queryKey, data] of snapshot) {
    if (!data) continue;
    let listChanged = false;
    const pages = data.pages.map((page) => {
      let pageChanged = false;
      const items: ModelSummary[] = [];
      for (const item of page.items) {
        const result = updater(item);
        if (result === undefined) {
          items.push(item);
          continue;
        }
        pageChanged = true;
        if (result !== null) items.push(result);
      }
      if (!pageChanged) return page;
      listChanged = true;
      return { ...page, items };
    });
    if (listChanged) queryClient.setQueryData<ListPages>(queryKey, { ...data, pages });
  }
  return snapshot;
}

function restoreListSnapshot(queryClient: QueryClient, snapshot: ListSnapshot): void {
  for (const [queryKey, data] of snapshot) {
    queryClient.setQueryData(queryKey, data);
  }
}

/** Finds the slugs of every cached list item whose id is in `ids` --
 * used so a bulk mutation (which only receives ids) can still snapshot and
 * optimistically patch the matching model-detail queries. Reads from a
 * snapshot taken before the mutation, not the live cache, so it reflects
 * the pre-patch state. */
function slugsForIds(snapshot: ListSnapshot, ids: ReadonlySet<number>): string[] {
  const slugs = new Set<string>();
  for (const [, data] of snapshot) {
    if (!data) continue;
    for (const page of data.pages) {
      for (const item of page.items) {
        if (ids.has(item.id)) slugs.add(item.slug);
      }
    }
  }
  return [...slugs];
}

/** Applies the fields a `ModelPatch` can carry that also exist on the
 * lighter-weight `ModelSummary` shown in the gallery grid -- `cover_blob_hash`
 * and `is_archived` aren't part of `ModelSummary`, so they're left for the
 * server round-trip (`cover_blob_hash` needs derivative computation anyway,
 * and `is_archived` is handled by `useArchiveModel`'s own optimistic path). */
function applyPatchToSummary(item: ModelSummary, payload: ModelPatch): ModelSummary {
  return {
    ...item,
    ...(payload.name !== undefined ? { name: payload.name } : {}),
    ...(payload.description !== undefined ? { description: payload.description } : {}),
    ...(payload.favorite !== undefined ? { favorite: payload.favorite } : {}),
    ...(payload.review_state !== undefined ? { review_state: payload.review_state } : {}),
  };
}

/** Folds a server-confirmed `ModelDetail` back into a cached `ModelSummary`
 * after a successful patch/archive, for the fields both shapes share. */
function mergeDetailIntoSummary(item: ModelSummary, detail: ModelDetail): ModelSummary {
  return {
    ...item,
    name: detail.name,
    description: detail.description,
    favorite: detail.favorite,
    review_state: detail.review_state,
    tags: detail.tags,
  };
}

export interface GalleryFilters {
  q?: string;
  /** Tag ids; `tagMode` picks any-of vs all-of. */
  tags?: number[];
  tagMode?: TagMode;
  /** Models having a FILE carrying these tag ids; `fileTagMode` is independent of `tagMode`. */
  fileTags?: number[];
  fileTagMode?: TagMode;
  format?: string;
  has_sliced?: boolean;
  collection?: number;
  favorite?: boolean;
  // feat/import-fidelity T4: the Archived facet. Unlike `favorite` (which
  // never hides anything when unset), the backend's `archived` param
  // defaults to `false` server-side -- so leaving this unset already gets
  // the "hide archived" default, and only `true` is ever worth sending.
  archived?: boolean;
  // R13b: single-select category facet -- ANDed with every other filter,
  // same as `collection`.
  category?: number;
  sort: string;
}

const GALLERY_PAGE_SIZE = 24;

export function buildModelsUrl(filters: Partial<GalleryFilters>, cursor?: string, limit = GALLERY_PAGE_SIZE): string {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  appendTagParams(params, filters.tags, filters.tagMode);
  if (filters.fileTags?.length) {
    for (const id of filters.fileTags) params.append("file_tags", String(id));
    if (filters.fileTagMode === "all") params.set("file_tag_mode", "all");
  }
  if (filters.format) params.set("format", filters.format);
  if (filters.has_sliced) params.set("has_sliced", "true");
  if (filters.collection !== undefined) params.set("collection", String(filters.collection));
  // `false`/omitted apply no filter at all (never hides favorites) --
  // mirrors the backend's `favorite` query param semantics.
  if (filters.favorite) params.set("favorite", "true");
  if (filters.archived) params.set("archived", "true");
  if (filters.category !== undefined) params.set("category", String(filters.category));
  if (filters.sort) params.set("sort", filters.sort);
  params.set("limit", String(limit));
  if (cursor) params.set("cursor", cursor);
  return `/models?${params.toString()}`;
}

/** Gallery grid — cursor-paginated via `useInfiniteQuery` (Task 8 decision). */
export function useModelsQuery(filters: GalleryFilters) {
  return useInfiniteQuery({
    queryKey: ["models", "list", filters] as const,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      api.get<GalleryPage>(buildModelsUrl(filters, pageParam)),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

/** Lightweight model search used by the upload page's "existing model" picker. */
export function useModelSearchQuery(q: string) {
  return useQuery({
    queryKey: ["models", "search", q] as const,
    queryFn: () => api.get<GalleryPage>(buildModelsUrl({ q, sort: "name" }, undefined, 10)),
    enabled: q.trim().length > 0,
  });
}

/** Most recently updated models -- the print dialog's "Recent models" list. */
export function useRecentModelsQuery(enabled = true) {
  return useQuery({
    queryKey: ["models", "recent"] as const,
    queryFn: () => api.get<GalleryPage>(buildModelsUrl({ sort: "-updated_at" }, undefined, 8)),
    enabled,
  });
}

/** The model detail page's "More from <collection>" strip (Task 2, collection
 * provenance) -- a small, non-paginated peek at a few other models imported
 * from the same followed collection. Disabled when the model has no
 * `source_collection_id` (manually created, or imported outside a followed
 * collection). */
export function useRelatedModelsQuery(collectionId: number | undefined) {
  return useQuery({
    queryKey: ["models", "related", collectionId] as const,
    queryFn: () => api.get<GalleryPage>(buildModelsUrl({ collection: collectionId }, undefined, 6)),
    enabled: collectionId !== undefined,
  });
}

export function modelQueryOptions(slug: string) {
  return queryOptions({
    queryKey: ["models", "detail", slug] as const,
    queryFn: () => api.get<ModelDetail>(`/models/${encodeURIComponent(slug)}`),
    enabled: slug.length > 0,
  });
}

export function useModel(slug: string) {
  return useQuery(modelQueryOptions(slug));
}

/** R13a viewer "Cover" action: POSTs a raw PNG screenshot (from the viewer's
 * `screenshot()` capture) to the cover-snapshot endpoint, which ingests it
 * through the normal upload pipeline and sets `model.cover_blob_hash` in one
 * transaction (`backend/app/api/models.py`'s `POST /models/{slug}/cover`).
 * Returns the updated `ModelDetail` -- callers invalidate the detail + list
 * queries themselves (see `useViewerScene`'s `captureCover`) since the
 * optimistic local object-URL swap needs to happen around the same await. */
export function uploadModelCover(slug: string, blob: Blob): Promise<ModelDetail> {
  return api.postRaw<ModelDetail>(`/models/${encodeURIComponent(slug)}/cover`, blob, "image/png");
}

export function useCreateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModelCreate) => api.post<ModelDetail>("/models", payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["models", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
  });
}

export function usePatchModel(slug: string) {
  const queryClient = useQueryClient();
  const detailKey = modelQueryOptions(slug).queryKey;
  return useMutation({
    mutationFn: (payload: ModelPatch) => api.patch<ModelDetail>(`/models/${slug}`, payload),
    onMutate: async (payload) => {
      await queryClient.cancelQueries({ queryKey: LIST_QUERY_KEY });
      const listSnapshot = patchModelInListCache(queryClient, LIST_QUERY_KEY, (item) =>
        item.slug === slug ? applyPatchToSummary(item, payload) : undefined,
      );
      const detailSnapshot = queryClient.getQueryData<ModelDetail>(detailKey);
      if (detailSnapshot) queryClient.setQueryData(detailKey, { ...detailSnapshot, ...payload });
      return { listSnapshot, detailSnapshot };
    },
    onError: (_err, _payload, context) => {
      if (!context) return;
      restoreListSnapshot(queryClient, context.listSnapshot);
      queryClient.setQueryData(detailKey, context.detailSnapshot);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(detailKey, data);
      patchModelInListCache(queryClient, LIST_QUERY_KEY, (item) =>
        item.slug === slug ? mergeDetailIntoSummary(item, data) : undefined,
      );
    },
    // Single-field patches (favorite/name/description) don't touch the list
    // query -- `onSuccess` above already folded the server response into
    // every cached list page, so a full list invalidation would just cost a
    // refetch (and re-render every card) for no new information.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: detailKey });
      // A patch can change fields the folder browser's tree strip shows
      // (name, category, tags) -- keep it from going stale too.
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
  });
}

/** `POST /models/bulk` (Branch 4 Task 1) -- applies the same tag/favorite
 * changes to every model in `ids` in one call, used by the library's bulk
 * select mode. Declared before `/{slug}`-scoped routes match on the
 * backend, but that's a server-side routing detail; from here it's just
 * another mutation. Invalidates the whole `["models"]` prefix (covers both
 * the gallery list and any open model-detail queries) since the affected
 * slugs aren't known client-side. */
export function useBulkUpdateModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModelBulkIn) => api.post<ModelBulkOut>("/models/bulk", payload),
    onMutate: async (payload) => {
      await queryClient.cancelQueries({ queryKey: LIST_QUERY_KEY });
      const idSet = new Set(payload.ids);
      const applyBulk = (tags: string[], favorite: boolean): { tags: string[]; favorite: boolean } => {
        let nextTags = tags;
        if (payload.add_tags?.length || payload.remove_tags?.length) {
          const set = new Set(tags);
          for (const tag of payload.add_tags ?? []) set.add(tag);
          for (const tag of payload.remove_tags ?? []) set.delete(tag);
          nextTags = [...set];
        }
        return { tags: nextTags, favorite: payload.favorite ?? favorite };
      };
      const listSnapshot = patchModelInListCache(queryClient, LIST_QUERY_KEY, (item) => {
        if (!idSet.has(item.id)) return undefined;
        return { ...item, ...applyBulk(item.tags, item.favorite) };
      });
      const slugs = slugsForIds(listSnapshot, idSet);
      const detailSnapshots = slugs.map(
        (slug) => [modelQueryOptions(slug).queryKey, queryClient.getQueryData<ModelDetail>(modelQueryOptions(slug).queryKey)] as const,
      );
      for (const [key, detail] of detailSnapshots) {
        if (!detail) continue;
        queryClient.setQueryData(key, { ...detail, ...applyBulk(detail.tags, detail.favorite) });
      }
      return { listSnapshot, detailSnapshots };
    },
    onError: (_err, _payload, context) => {
      if (!context) return;
      restoreListSnapshot(queryClient, context.listSnapshot);
      for (const [key, data] of context.detailSnapshots) queryClient.setQueryData(key, data);
    },
    onSettled: (_data, _err, _payload, context) => {
      void queryClient.invalidateQueries({ queryKey: LIST_QUERY_KEY });
      for (const [key] of context?.detailSnapshots ?? []) void queryClient.invalidateQueries({ queryKey: key });
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
  });
}

/** `POST /models/bulk-delete` (Round 11 T1/T3) -- moves every model in
 * `ids` in one call, used by the library's bulk select mode. Takes `slugs`
 * alongside `ids` (unused by the request body itself) so `onSuccess` can
 * drop each deleted model's own detail query the same way `useDeleteModel`
 * does -- nothing left to keep cached for a hard-deleted model -- while also
 * invalidating the whole `["models"]` prefix like `useBulkUpdateModels`
 * above, which covers the gallery list. The prefix invalidation runs in
 * `onSettled`, not `onSuccess`: the server commits per model, so a failed
 * request can still have deleted some of the batch -- only a refetch
 * reconciles the gallery either way (window-focus refetch is off globally,
 * so nothing else would). */
export function useBulkDeleteModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ids }: { ids: number[]; slugs: string[] }) =>
      api.post<ModelBulkDeleteOut>("/models/bulk-delete", { ids }),
    onMutate: async ({ ids }) => {
      await queryClient.cancelQueries({ queryKey: LIST_QUERY_KEY });
      const idSet = new Set(ids);
      const listSnapshot = patchModelInListCache(queryClient, LIST_QUERY_KEY, (item) =>
        idSet.has(item.id) ? null : undefined,
      );
      return { listSnapshot };
    },
    onError: (_err, _payload, context) => {
      if (context) restoreListSnapshot(queryClient, context.listSnapshot);
    },
    onSuccess: (_data, { slugs }) => {
      for (const slug of slugs) {
        queryClient.removeQueries({ queryKey: modelQueryOptions(slug).queryKey });
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: LIST_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
      void queryClient.invalidateQueries({ queryKey: ["trash"] });
    },
  });
}

/** Archives/unarchives a model (feat/import-fidelity T3: `PATCH
 * {is_archived}` -- reversible, and no longer what `DELETE /models/{slug}`
 * does). Takes the target `is_archived` value as the mutate argument so the
 * same hook drives both the header's "Archive" action (`true`) and the
 * archived-model banner's "Unarchive" action (`false`). */
export function useArchiveModel(slug: string) {
  const queryClient = useQueryClient();
  const detailKey = modelQueryOptions(slug).queryKey;
  return useMutation({
    mutationFn: (is_archived: boolean) => api.patch<ModelDetail>(`/models/${slug}`, { is_archived }),
    onMutate: async (is_archived) => {
      await queryClient.cancelQueries({ queryKey: LIST_QUERY_KEY });
      // Archiving removes the card from every cached list page (mirrors the
      // backend's default `archived=false` filter). Unarchiving doesn't try
      // to reinsert it -- position/sort is server-owned -- `onSettled`'s
      // list invalidation below picks it back up on refetch instead.
      const listSnapshot = patchModelInListCache(queryClient, LIST_QUERY_KEY, (item) =>
        item.slug === slug && is_archived ? null : undefined,
      );
      const detailSnapshot = queryClient.getQueryData<ModelDetail>(detailKey);
      if (detailSnapshot) queryClient.setQueryData(detailKey, { ...detailSnapshot, is_archived });
      return { listSnapshot, detailSnapshot };
    },
    onError: (_err, _payload, context) => {
      if (!context) return;
      restoreListSnapshot(queryClient, context.listSnapshot);
      queryClient.setQueryData(detailKey, context.detailSnapshot);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(detailKey, data);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: detailKey });
      void queryClient.invalidateQueries({ queryKey: LIST_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
  });
}

/** `DELETE /models/{slug}` moves the model to the trash (restorable via
 * `api/trash.ts`); the detail query is dropped since the slug now 404s. */
export function useDeleteModel(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>(`/models/${slug}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: modelQueryOptions(slug).queryKey });
      void queryClient.invalidateQueries({ queryKey: ["models", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
      void queryClient.invalidateQueries({ queryKey: ["trash"] });
    },
  });
}

/** Dispatches `POST /models/{slug}/redownload` (feat/import-fidelity T3) to
 * re-fetch this model's files fresh from its original import source, either
 * as a new revision or in place. Returns the tracked `JobOut` -- no explicit
 * invalidation here: `useEvents.tsx`'s `job.updated` handler already
 * invalidates `["models"]`/`["revisions"]` on every job's terminal state
 * (not gated by job type), which is how the Files/Revisions tabs pick up the
 * redownloaded files, same as `useRelocateModel` below. */
export function useRedownloadModel(slug: string) {
  return useMutation({
    mutationFn: (payload: ModelRedownloadIn) =>
      api.post<JobOut>(`/models/${encodeURIComponent(slug)}/redownload`, payload),
  });
}

/** Dispatches `POST /models/{slug}/relocate` (Workstream C task C3/C4) to
 * move or replicate every file of this model, across all its revisions,
 * onto another configured backend. Returns the tracked `JobOut` -- no
 * explicit `["models"]`/detail invalidation here: `useEvents.tsx`'s
 * `job.updated` handler already invalidates `["models"]` on every job's
 * terminal state, which is how `model.backends` (Part 1) picks up a
 * completed "move". The storage tree isn't covered by that event handler
 * though (it's not a `["models"]` query), so it's invalidated directly here
 * once the relocate job is dispatched -- a `["storage"]` refetch is cheap
 * and the job's terminal state isn't tracked client-side to gate it on. */
export function useRelocateModel(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModelRelocateIn) =>
      api.post<JobOut>(`/models/${encodeURIComponent(slug)}/relocate`, payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["storage"] }),
  });
}

export function useRevisions(modelId: number | undefined) {
  return useQuery({
    queryKey: ["revisions", modelId] as const,
    queryFn: () => api.get<RevisionSummary[]>(`/models/${modelId}/revisions`),
    enabled: modelId !== undefined,
  });
}

export function useCreateRevision(slug: string, modelId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: RevisionCreate) =>
      api.post<RevisionDetail>(`/models/${modelId}/revisions`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey });
      void queryClient.invalidateQueries({ queryKey: ["revisions", modelId] });
    },
  });
}

export function useRevisionDiff(aId: number | undefined, bId: number | undefined) {
  return useQuery({
    queryKey: ["diff", aId, bId] as const,
    queryFn: () => api.get<DiffResponse>(`/revisions/${aId}/diff/${bId}`),
    enabled: aId !== undefined && bId !== undefined,
  });
}

/** `GET /revisions/{id}` — the only endpoint that returns a revision's own
 * notes (`RevisionSummary`, used for the history list, doesn't carry them). */
export function revisionDetailQueryOptions(revisionId: number) {
  return queryOptions({
    queryKey: ["revisions", "detail", revisionId] as const,
    queryFn: () => api.get<RevisionDetail>(`/revisions/${revisionId}`),
  });
}

export function useRevisionDetail(revisionId: number) {
  return useQuery(revisionDetailQueryOptions(revisionId));
}

/** Per-revision note mutations — same `/notes` endpoints as the model-level
 * hooks below, but invalidating the revision detail query (which is where
 * a revision's notes actually live) instead of the model query. */
export function useCreateRevisionNote(revisionId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: NoteCreate) => api.post<NoteOut>(`/notes`, payload),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: revisionDetailQueryOptions(revisionId).queryKey }),
  });
}

export function usePatchRevisionNote(revisionId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: string }) => api.patch<NoteOut>(`/notes/${id}`, { body }),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: revisionDetailQueryOptions(revisionId).queryKey }),
  });
}

export function useDeleteRevisionNote(revisionId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/notes/${id}`),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: revisionDetailQueryOptions(revisionId).queryKey }),
  });
}

export function useCreateNote(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: NoteCreate) => api.post(`/notes`, payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey }),
  });
}

export function usePatchNote(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: string }) => api.patch(`/notes/${id}`, { body }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey }),
  });
}

export function useDeleteNote(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/notes/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey }),
  });
}

export function useDeleteFile(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/files/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey }),
  });
}
