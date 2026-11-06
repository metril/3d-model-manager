import { MutationCache, QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, setUnauthorizedHandler } from "@/api/client";

/** Retry transient failures only: network errors (fetch TypeError) and 5xx,
 * never 4xx (auth, validation, not-found are not going to fix themselves). */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (error instanceof ApiError) return error.status >= 500;
  return error instanceof TypeError;
}

export const queryClient = new QueryClient({
  // Global fallback so actions without their own inline error UI (archive,
  // delete file, tag add/remove, notes) still surface failures instead of
  // failing silently. Forms with inline error text (login, new
  // model/revision dialogs) get a toast too, which is a harmless bonus.
  //
  // `meta.silentError` opts a mutation out: a caller that fans the SAME
  // mutation out over a selection (the library's bulk "Add to queue" loop)
  // reports one summary toast itself, and would otherwise stack a
  // per-failure toast on top of it for every model in the batch.
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      if (mutation.meta?.silentError) return;
      toast.error(error instanceof ApiError ? error.detail : "Something went wrong");
    },
  }),
  defaultOptions: {
    queries: {
      retry: shouldRetry,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
});

// A 401 means the session is gone: drop every cached query so route guards
// can't pass from stale cache after the redirect.
setUnauthorizedHandler(() => queryClient.clear());
