/**
 * Universal tags: one global tag list (name, hex color, lucide icon,
 * description) attached to models, individual model files, projects,
 * project attachments, print archives and followed collections.
 */
import { queryOptions, useMutation, useQuery, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { TagCreate, TagOut, TagRef, TagUpdate } from "@/api/types";

export type TagMode = "any" | "all";

/** What a tag set is being replaced on. */
export type TagTarget =
  | { kind: "model"; id: number }
  | { kind: "file"; modelId: number; relPath: string }
  | { kind: "project"; id: number }
  | { kind: "attachment"; projectId: number; id: number }
  | { kind: "archive"; id: number }
  | { kind: "collection"; id: number };

export const tagsQueryOptions = queryOptions({
  queryKey: ["tags"] as const,
  queryFn: () => api.get<TagOut[]>("/tags?with_counts=true"),
});

export function useTags() {
  return useQuery(tagsQueryOptions);
}

/** Appends the repeated `tags=<id>` + `tag_mode` filter params. */
export function appendTagParams(params: URLSearchParams, tags: number[] | undefined, mode: TagMode | undefined): void {
  if (!tags?.length) return;
  for (const id of tags) params.append("tags", String(id));
  if (mode === "all") params.set("tag_mode", "all");
}

/** Everything that embeds `tag_refs`; refetched after a tag is edited/merged/deleted. */
const TAGGED_PREFIXES: QueryKey[] = [["models"], ["projects"], ["archives"], ["collections"], ["printers", "overview"]];

function invalidateTagged(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: tagsQueryOptions.queryKey });
  for (const queryKey of TAGGED_PREFIXES) void queryClient.invalidateQueries({ queryKey });
}

export function useCreateTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TagCreate) => api.post<TagOut>("/tags", body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: tagsQueryOptions.queryKey }),
  });
}

export function useUpdateTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...patch }: TagUpdate & { id: number }) => api.patch<TagOut>(`/tags/${id}`, patch),
    onSuccess: () => invalidateTagged(queryClient),
  });
}

export function useDeleteTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/tags/${id}`),
    onSuccess: () => invalidateTagged(queryClient),
  });
}

export function useMergeTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, intoId }: { id: number; intoId: number }) =>
      api.post<TagOut>(`/tags/${id}/merge`, { into_id: intoId }),
    onSuccess: () => invalidateTagged(queryClient),
  });
}

// -- per-entity replace-set mutation --------------------------------------

type Rec = Record<string, unknown>;

/** Maps `fn` over every entity in a cached payload: a bare array, an
 * infinite-query `{pages}` / `{items}` page, or a single detail object.
 * `fn` returns a replacement or `undefined` to leave the entity alone. */
function mapEntities(data: unknown, fn: (entity: Rec) => Rec | undefined): unknown {
  if (Array.isArray(data)) return data.map((entry) => mapEntities(entry, fn));
  if (!data || typeof data !== "object") return data;
  const obj = data as Rec;
  if (Array.isArray(obj.pages)) return { ...obj, pages: obj.pages.map((page) => mapEntities(page, fn)) };
  if (Array.isArray(obj.items) && !("tag_refs" in obj)) return { ...obj, items: mapEntities(obj.items, fn) };
  return fn(obj) ?? obj;
}

function withRefs(entity: Rec, refs: TagRef[]): Rec {
  return { ...entity, tag_refs: refs, ...("tags" in entity ? { tags: refs.map((ref) => ref.name) } : {}) };
}

function patchPlan(target: TagTarget, refs: TagRef[]): { prefix: QueryKey; fn: (entity: Rec) => Rec | undefined } {
  const hasRefs = (e: Rec) => "tag_refs" in e;
  switch (target.kind) {
    case "model":
      return { prefix: ["models"], fn: (e) => (e.id === target.id && hasRefs(e) ? withRefs(e, refs) : undefined) };
    case "file":
      return {
        prefix: ["models"],
        fn: (e) => {
          const rev = e.current_revision as Rec | null | undefined;
          if (e.id !== target.modelId || !rev || !Array.isArray(rev.files)) return undefined;
          const files = (rev.files as Rec[]).map((f) => (f.rel_path === target.relPath ? { ...f, tag_refs: refs } : f));
          return { ...e, current_revision: { ...rev, files } };
        },
      };
    case "project":
      return { prefix: ["projects"], fn: (e) => (e.id === target.id && hasRefs(e) ? withRefs(e, refs) : undefined) };
    case "attachment":
      return {
        prefix: ["projects", "detail", target.projectId],
        fn: (e) => {
          if (!Array.isArray(e.attachments)) return undefined;
          const attachments = (e.attachments as Rec[]).map((a) => (a.id === target.id ? { ...a, tag_refs: refs } : a));
          return { ...e, attachments };
        },
      };
    case "archive":
      return { prefix: ["archives"], fn: (e) => (e.id === target.id && hasRefs(e) ? withRefs(e, refs) : undefined) };
    case "collection":
      return { prefix: ["collections"], fn: (e) => (e.id === target.id && hasRefs(e) ? withRefs(e, refs) : undefined) };
  }
}

function setPath(target: TagTarget): string {
  switch (target.kind) {
    case "model":
      return `/models/${target.id}/tags`;
    case "file":
      return `/models/${target.modelId}/files/tags`;
    case "project":
      return `/projects/${target.id}/tags`;
    case "attachment":
      return `/projects/${target.projectId}/attachments/${target.id}/tags`;
    case "archive":
      return `/archives/${target.id}/tags`;
    case "collection":
      return `/collections/${target.id}/tags`;
  }
}

export interface SetTagsVars {
  target: TagTarget;
  /** The full desired tag set (replace semantics). */
  refs: TagRef[];
}

/** `PUT …/tags {tag_ids}` for any taggable entity, with an optimistic
 * cache patch (rolled back on error) and invalidation afterwards. One hook
 * instance serves many rows -- the target is a mutate-time variable. */
export function useSetEntityTags() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ target, refs }: SetTagsVars) => {
      const tag_ids = refs.map((ref) => ref.id);
      const body = target.kind === "file" ? { rel_path: target.relPath, tag_ids } : { tag_ids };
      return api.put<TagRef[]>(setPath(target), body);
    },
    onMutate: async ({ target, refs }) => {
      const { prefix, fn } = patchPlan(target, refs);
      await queryClient.cancelQueries({ queryKey: prefix });
      const snapshot = queryClient.getQueriesData({ queryKey: prefix });
      queryClient.setQueriesData({ queryKey: prefix }, (old) => (old === undefined ? old : mapEntities(old, fn)));
      return { snapshot };
    },
    onError: (_err, _vars, context) => {
      for (const [key, data] of context?.snapshot ?? []) queryClient.setQueryData(key, data);
    },
    onSettled: (_data, _err, { target }) => {
      void queryClient.invalidateQueries({ queryKey: tagsQueryOptions.queryKey });
      void queryClient.invalidateQueries({ queryKey: patchPlan(target, []).prefix });
    },
  });
}
