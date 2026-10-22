/**
 * Finance hooks (P7 review/billing plan Task 8): the `billing_enabled`
 * config toggle, cost-center CRUD, ledger pages and deposits/adjustments.
 * Mirrors `queue.ts`'s hook conventions -- `useQuery`/`useMutation` +
 * `queryClient.invalidateQueries`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  CostCenter,
  CostCenterCreateIn,
  CostCenterPatchIn,
  DepositIn,
  FinanceConfig,
  LedgerEntry,
} from "@/api/types";

export function useFinanceConfig() {
  return useQuery({
    queryKey: ["finance", "config"] as const,
    queryFn: () => api.get<FinanceConfig>("/finance/config"),
  });
}

export function useSetBilling() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (billing_enabled: boolean) =>
      api.put<FinanceConfig>("/finance/config", { billing_enabled }),
    onSuccess: (data) => queryClient.setQueryData(["finance", "config"], data),
  });
}

export function useCostCenters({ mine = false }: { mine?: boolean } = {}) {
  return useQuery({
    queryKey: ["finance", "cost-centers", { mine }] as const,
    queryFn: () => api.get<CostCenter[]>(`/finance/cost-centers${mine ? "?mine=true" : ""}`),
  });
}

export function useCostCenter(id: number | null) {
  return useQuery({
    queryKey: ["finance", "cost-centers", id] as const,
    queryFn: () => api.get<CostCenter>(`/finance/cost-centers/${id}`),
    enabled: id != null,
  });
}

export function useLedger(id: number | null) {
  return useQuery({
    queryKey: ["finance", "ledger", id] as const,
    queryFn: () => api.get<LedgerEntry[]>(`/finance/ledger?cost_center=${id}`),
    enabled: id != null,
  });
}

function invalidateCostCenters(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["finance", "cost-centers"] });
}

export function useSaveCostCenter() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: CostCenterCreateIn | CostCenterPatchIn }) =>
      id != null
        ? api.patch<CostCenter>(`/finance/cost-centers/${id}`, body)
        : api.post<CostCenter>("/finance/cost-centers", body as CostCenterCreateIn),
    onSuccess: () => invalidateCostCenters(queryClient),
  });
}

export function useDeleteCostCenter() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/finance/cost-centers/${id}`),
    onSuccess: () => invalidateCostCenters(queryClient),
  });
}

export function useDeposit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: DepositIn) => api.post<LedgerEntry>("/finance/deposits", body),
    onSuccess: (_data, variables) => {
      invalidateCostCenters(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["finance", "ledger", variables.cost_center_id] });
    },
  });
}
