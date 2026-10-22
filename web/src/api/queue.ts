/**
 * Print queue (backend/app/schemas/queue.py, Branch 4 Task 1; extended by
 * the P6 "slicer, profiles and dispatch" plan, Task 6/10, with per-file
 * dispatch columns and retry). Mirrors `library.ts`'s hook conventions --
 * `useQuery`/`useMutation` + `queryClient.invalidateQueries`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { QueueEnqueueBody, QueueEntry, ReviewCountOut, ReviewIn } from "@/api/types";

// The default (`includeDone: false`) case keeps the original bare `["queue"]`
// key -- `PrintsTab.test.tsx` (and anything else reading the cache
// directly rather than through `useQueue`) depends on that exact shape.
function queueQueryKey(includeDone: boolean) {
  return includeDone ? (["queue", "done"] as const) : (["queue"] as const);
}

/** The whole queue, ordered by position (1-based). `includeDone` also
 * returns terminal (`done`) entries, which the backend otherwise omits. */
export function useQueue({ includeDone = false }: { includeDone?: boolean } = {}) {
  return useQuery({
    queryKey: queueQueryKey(includeDone),
    queryFn: () => api.get<QueueEntry[]>(`/queue${includeDone ? "?include_done=true" : ""}`),
  });
}

function invalidateQueue(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["queue"] });
  // P3 Task 3: the overview's `up_next` is the queue's head -- keep it
  // in sync with every queue mutation, not just a manual overview poll.
  void queryClient.invalidateQueries({ queryKey: ["printers", "overview"] });
}

/** `POST /queue` -- the general per-file enqueue (P6 Task 6): a `file_id`
 * entry (optionally plate/printer/recipe/copies/ams_mapping/options) is
 * never idempotent, always creating a new row. */
export function useEnqueue() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: QueueEnqueueBody) => api.post<QueueEntry>("/queue", body),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

/** `POST /queue` -- appends a bare model-only entry to the end of the
 * queue. Idempotent: the backend answers 200 (not 201) if the model is
 * already queued instead of erroring or duplicating it -- either way this
 * resolves, so callers don't need to special-case "already queued" as a
 * failure.
 *
 * `silentError` suppresses the global `MutationCache` error toast
 * (`queryClient.ts`) for callers that fan this mutation out over a
 * selection and report one summary toast of their own -- without it, the
 * library's bulk "Add to queue" would toast once per failed model AND once
 * for the batch. */
export function useEnqueueModel({ silentError = false }: { silentError?: boolean } = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelId: number) => api.post<QueueEntry>("/queue", { model_id: modelId }),
    onSuccess: () => invalidateQueue(queryClient),
    meta: { silentError },
  });
}

export function useRemoveQueueEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entryId: number) => api.delete<void>(`/queue/${entryId}`),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

/** `PATCH /queue/{entry_id}` -- moves the entry to a 1-based `position`
 * (clamped server-side to `[1, n]`), returning the whole reordered queue. */
export function useMoveQueueEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entryId, position }: { entryId: number; position: number }) =>
      api.patch<QueueEntry[]>(`/queue/${entryId}`, { position }),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

/** `POST /queue/{entry_id}/retry` -- re-arms a `failed`/`held` entry (P6
 * Task 6) back to `waiting` so the dispatcher picks it up again. */
export function useRetryQueueEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entryId: number) => api.post<QueueEntry>(`/queue/${entryId}/retry`),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

// -- review gate (P7 review/billing plan Task 6/9) -------------------------

/** `GET /queue/review-inbox` -- pending items the caller may act on as a
 * reviewer. `queue:review`-gated server-side; callers should only mount
 * this for a holder of that permission. */
export function useReviewInbox() {
  return useQuery({
    queryKey: ["queue", "review-inbox"] as const,
    queryFn: () => api.get<QueueEntry[]>("/queue/review-inbox"),
  });
}

/** `GET /queue/review-inbox/count` -- polled every 30s for the sidebar
 * badge/dashboard card, only while `enabled` (the caller holds
 * `queue:review` -- the endpoint 403s otherwise). */
export function useReviewCount({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["queue", "review-inbox", "count"] as const,
    queryFn: () => api.get<ReviewCountOut>("/queue/review-inbox/count"),
    enabled,
    refetchInterval: enabled ? 30_000 : false,
  });
}

/** `POST /queue/{entry_id}/review` -- approve/reject, invalidating both the
 * review inbox/count and the plain queue (a rejected/approved item's
 * `review_state` changes there too). */
export function useReviewEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entryId, body }: { entryId: number; body: ReviewIn }) =>
      api.post<QueueEntry>(`/queue/${entryId}/review`, body),
    onSuccess: () => {
      invalidateQueue(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["queue", "review-inbox"] });
    },
  });
}
