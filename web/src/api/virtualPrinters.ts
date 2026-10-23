/** Query/mutation hooks for `/api/virtual-printers` (Q11). */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { VirtualPrinter, VirtualPrinterIn, VirtualPrinterPatchIn } from "@/api/types";

export const virtualPrintersQueryOptions = queryOptions({
  queryKey: ["virtual-printers"] as const,
  queryFn: () => api.get<VirtualPrinter[]>("/virtual-printers"),
});

export function useVirtualPrinters() {
  return useQuery(virtualPrintersQueryOptions);
}

export function useCreateVirtualPrinter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: VirtualPrinterIn) => api.post<VirtualPrinter>("/virtual-printers", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: virtualPrintersQueryOptions.queryKey }),
  });
}

export function useUpdateVirtualPrinter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: VirtualPrinterPatchIn }) =>
      api.patch<VirtualPrinter>(`/virtual-printers/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: virtualPrintersQueryOptions.queryKey }),
  });
}

export function useDeleteVirtualPrinter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/virtual-printers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: virtualPrintersQueryOptions.queryKey }),
  });
}
