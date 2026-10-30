/**
 * Print queue (backend/app/schemas/queue.py, Branch 4 Task 1; extended by
 * the P6 "slicer, profiles and dispatch" plan, Task 6/10, with per-file
 * dispatch columns and retry). Mirrors `library.ts`'s hook conventions --
 * `useQuery`/`useMutation` + `queryClient.invalidateQueries`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  Batch,
  BatchIn,
  BatchPatch,
  QueueEnqueueBody,
  QueueEntry,
  QueuePreheatSettings,
  QueueSettings,
  QueueSchedulingFields,
  ReviewCountOut,
  ReviewIn,
} from "@/api/types";

// The default (`includeDone: false`) case keeps the original bare `["queue"]`
// key -- `PrintsTab.test.tsx` (and anything else reading the cache
// directly rather than through `useQueue`) depends on that exact shape.
function queueQueryKey(includeDone: boolean, batchId?: number | null) {
  if (batchId != null) return ["queue", "batch", batchId, includeDone] as const;
  return includeDone ? (["queue", "done"] as const) : (["queue"] as const);
}

/** The whole queue, ordered by position (1-based). `includeDone` also
 * returns terminal (`done`) entries, which the backend otherwise omits. */
export function useQueue({
  includeDone = false,
  batchId,
  enabled = true,
}: { includeDone?: boolean; batchId?: number | null; enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queueQueryKey(includeDone, batchId),
    queryFn: () => {
      const qs: string[] = [];
      if (includeDone) qs.push("include_done=true");
      if (batchId != null) qs.push(`batch_id=${batchId}`);
      return api.get<QueueEntry[]>(`/queue${qs.length ? `?${qs.join("&")}` : ""}`);
    },
    enabled,
  });
}

function invalidateQueue(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["queue"] });
  void queryClient.invalidateQueries({ queryKey: ["batches"] });
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

/** `POST /queue/{entry_id}/cancel-slice` -- kills an in-flight slice (the
 * entry becomes `failed`/"cancelled" and can be retried). */
export function useCancelQueueSlice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entryId: number) => api.post<QueueEntry>(`/queue/${entryId}/cancel-slice`),
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

// -- T4 scheduling ---------------------------------------------------------

/** `PATCH /queue/{id}` with scheduling fields (only sent keys applied,
 * `null` clears) -- returns the whole ordered queue. */
export function usePatchQueueEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entryId, body }: { entryId: number; body: Omit<QueueSchedulingFields, "batch_id"> }) =>
      api.patch<QueueEntry[]>(`/queue/${entryId}`, body),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

/** `POST /queue/{id}/start` (`queue:review`) -- releases a manual-start entry. */
export function useStartQueueEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entryId: number) => api.post<QueueEntry>(`/queue/${entryId}/start`),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

/** `POST /queue/{id}/cancel-schedule` -- clears `scheduled_at` (back to ASAP). */
export function useCancelSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entryId: number) => api.post<QueueEntry>(`/queue/${entryId}/cancel-schedule`),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

/** `POST /printers/{id}/queue/resume` (`queue:review`) -- SKIPPED -> WAITING. */
export function useResumePrinterQueue() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (printerId: number) =>
      api.post<{ resumed: number }>(`/printers/${printerId}/queue/resume`),
    onSuccess: () => invalidateQueue(queryClient),
  });
}

// -- batches (`queue:review`) ----------------------------------------------

const BATCHES_KEY = ["batches"] as const;

export function useBatches() {
  return useQuery({ queryKey: BATCHES_KEY, queryFn: () => api.get<Batch[]>("/batches") });
}

export function useBatch(batchId: number | null) {
  return useQuery({
    queryKey: [...BATCHES_KEY, batchId] as const,
    queryFn: () => api.get<Batch>(`/batches/${batchId}`),
    enabled: batchId != null,
  });
}

function invalidateBatches(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: BATCHES_KEY });
  invalidateQueue(queryClient);
}

export function useCreateBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BatchIn) => api.post<Batch>("/batches", body),
    onSuccess: () => invalidateBatches(queryClient),
  });
}

export function usePatchBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ batchId, body }: { batchId: number; body: BatchPatch }) =>
      api.patch<Batch>(`/batches/${batchId}`, body),
    onSuccess: () => invalidateBatches(queryClient),
  });
}

/** First call cancels (row kept); a second call on a cancelled batch removes it. */
export function useDeleteBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (batchId: number) => api.delete<void>(`/batches/${batchId}`),
    onSuccess: () => invalidateBatches(queryClient),
  });
}

/** 409 if the batch is not a draft. */
export function useDispatchBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (batchId: number) => api.post<Batch>(`/batches/${batchId}/dispatch`),
    onSuccess: () => invalidateBatches(queryClient),
  });
}

export function useUngroupBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (batchId: number) => api.post<Batch>(`/batches/${batchId}/ungroup`),
    onSuccess: () => invalidateBatches(queryClient),
  });
}

// -- preheat settings (`settings:write` to PUT) ----------------------------

const PREHEAT_KEY = ["settings", "queue-preheat"] as const;

export function useQueuePreheatSettings() {
  return useQuery({
    queryKey: PREHEAT_KEY,
    queryFn: () => api.get<QueuePreheatSettings>("/settings/queue-preheat"),
  });
}

export function useUpdateQueuePreheatSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: QueuePreheatSettings) => api.put<QueuePreheatSettings>("/settings/queue-preheat", body),
    onSuccess: (data) => queryClient.setQueryData(PREHEAT_KEY, data),
  });
}

// -- queue behaviour settings (`settings:write` to PUT) ---------------------

const QUEUE_SETTINGS_KEY = ["settings", "queue"] as const;

export function useQueueSettings() {
  return useQuery({
    queryKey: QUEUE_SETTINGS_KEY,
    queryFn: () => api.get<QueueSettings>("/settings/queue"),
  });
}

export function useUpdateQueueSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<QueueSettings>) => api.put<QueueSettings>("/settings/queue", body),
    onSuccess: (data) => queryClient.setQueryData(QUEUE_SETTINGS_KEY, data),
  });
}
