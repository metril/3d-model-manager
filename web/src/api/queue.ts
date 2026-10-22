/**
 * Print queue (backend/app/schemas/queue.py, Branch 4 Task 1): an ordered
 * "models to print" worklist. Mirrors `library.ts`'s hook conventions --
 * `useQuery`/`useMutation` + `queryClient.invalidateQueries`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { QueueEntry } from "@/api/types";

const queueQueryKey = ["queue"] as const;

/** The whole queue, ordered by position (1-based). */
export function useQueue() {
  return useQuery({
    queryKey: queueQueryKey,
    queryFn: () => api.get<QueueEntry[]>("/queue"),
  });
}

/** `POST /queue` -- appends a model to the end of the queue. Idempotent:
 * the backend answers 200 (not 201) if the model is already queued instead
 * of erroring or duplicating it -- either way this resolves, so callers
 * don't need to special-case "already queued" as a failure.
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
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queueQueryKey });
      // P3 Task 3: the overview's `up_next` is the queue's head -- keep it
      // in sync with every queue mutation, not just a manual overview poll.
      void queryClient.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
    meta: { silentError },
  });
}

export function useRemoveQueueEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entryId: number) => api.delete<void>(`/queue/${entryId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queueQueryKey });
      void queryClient.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
  });
}

/** `PATCH /queue/{entry_id}` -- moves the entry to a 1-based `position`
 * (clamped server-side to `[1, n]`), returning the whole reordered queue. */
export function useMoveQueueEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entryId, position }: { entryId: number; position: number }) =>
      api.patch<QueueEntry[]>(`/queue/${entryId}`, { position }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queueQueryKey });
      void queryClient.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
  });
}
