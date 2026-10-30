/**
 * Print plan / send flow hooks (P5 Task 11, `PrintDialog`): preview a
 * sliced file's plate/filament mapping against a printer (`POST
 * /print/plan`), actually send it (`POST /print/send`), and seed the plan
 * from an archived run (`POST /archives/{id}/reprint`) when relaunching
 * one. `usePrintPlan` is a query (not a mutation) even though the endpoint
 * is a POST -- the dialog re-derives its body from plate/printer/overrides
 * state and expects a fresh plan whenever any of those change, which is
 * exactly `useQuery`'s refetch-on-key-change behavior; `queryKey` is the
 * whole body so a shallow change re-fetches.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { PlanIn, PlanOut, PrintJobOut, SendIn } from "@/api/types";

export function usePrintPlan(body: PlanIn | null) {
  return useQuery({
    queryKey: ["print-plan", body] as const,
    queryFn: () => api.post<PlanOut>("/print/plan", body as PlanIn),
    enabled: body != null,
  });
}

export function useSendPrint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SendIn) => api.post<PrintJobOut>("/print/send", body),
    // The expected 409 `queue_required` is handled by the dialog (falls back
    // to enqueue); other failures render inline there -- no global toast.
    meta: { silentError: true },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["print-jobs"] });
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
  });
}

/** `POST /archives/{id}/reprint` -- the initial plan for a relaunch of an
 * archived run; 409s when the archived file isn't in the library any
 * more (the dialog surfaces that as an error, no Reprint retry). */
export function useReprint(archiveId: number | null) {
  return useQuery({
    queryKey: ["print-plan", "reprint", archiveId] as const,
    queryFn: () => api.post<PlanOut>(`/archives/${archiveId}/reprint`),
    enabled: archiveId != null,
    retry: false,
  });
}
