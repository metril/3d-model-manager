/**
 * Query/mutation hooks for the spool inventory domain (P5 Task 9):
 * `/api/spools` CRUD + manual usage, and `useAssignSlot` for `PUT
 * /printers/{id}/slots/{unit}/{slot}` (owned by `app.api.printers`
 * server-side, but it's spool data from the client's point of view --
 * `SlotAssignPopover`, Task 10, is its only caller). Mirrors
 * `materials.ts`'s hooks shape.
 */
import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  ManualUsageIn,
  SlotAssignIn,
  SpoolCreate,
  SpoolDetailOut,
  SpoolOut,
  SpoolUpdate,
  SpoolUsageRow,
} from "@/api/types";

export interface SpoolFilters {
  archived?: boolean;
  low_stock?: boolean;
  material_type?: string;
  q?: string;
  needs_drying?: boolean;
}

function spoolsQueryKey(filters?: SpoolFilters) {
  return ["spools", filters ?? {}] as const;
}

function spoolsSearch(filters?: SpoolFilters): string {
  const params = new URLSearchParams();
  if (filters?.archived) params.set("archived", "true");
  if (filters?.low_stock != null) params.set("low_stock", String(filters.low_stock));
  if (filters?.material_type) params.set("material_type", filters.material_type);
  if (filters?.q) params.set("q", filters.q);
  if (filters?.needs_drying) params.set("needs_drying", "true");
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function useSpools(filters?: SpoolFilters) {
  return useQuery({
    queryKey: spoolsQueryKey(filters),
    queryFn: () => api.get<SpoolOut[]>(`/spools${spoolsSearch(filters)}`),
    placeholderData: keepPreviousData,
  });
}

export function spoolQueryOptions(id: number) {
  return queryOptions({
    queryKey: ["spools", "detail", id] as const,
    queryFn: () => api.get<SpoolDetailOut>(`/spools/${id}`),
  });
}

export function useSpool(id: number | null | undefined) {
  return useQuery({ ...spoolQueryOptions(id ?? -1), enabled: id != null });
}

export function useCreateSpool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SpoolCreate) => api.post<SpoolOut>("/spools", payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

export function usePatchSpool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: SpoolUpdate }) =>
      api.patch<SpoolOut>(`/spools/${id}`, payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

export function useDeleteSpool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/spools/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

export function useAddUsage(spoolId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ManualUsageIn) => api.post<SpoolUsageRow>(`/spools/${spoolId}/usage`, payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["spools"] }),
  });
}

/** `PUT /printers/{id}/slots/{unit}/{slot}` -- assign (or clear, with
 * `spool_id: null`) the spool a tray's RFID/manual mapping points at.
 * `unit`/`slot` are the printer's own per-unit AMS coordinates (255/254
 * for the external-spool sentinel), not the global tray id. */
export function useAssignSlot(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ unit, slot, payload }: { unit: number; slot: number; payload: SlotAssignIn }) =>
      api.put<void>(`/printers/${printerId}/slots/${unit}/${slot}`, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers", printerId, "status"] });
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
      void qc.invalidateQueries({ queryKey: ["spools"] });
    },
  });
}
