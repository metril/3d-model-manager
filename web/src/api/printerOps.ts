/** Q5 hooks: HMS, firmware, connection diagnostics, discovery. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  DiagnosticIn,
  DiagnosticReport,
  DiscoveredPrinter,
  FirmwareInfo,
  HmsFault,
} from "@/api/types";

export function useHms(printerId: number, enabled = true) {
  return useQuery({
    queryKey: ["printers", printerId, "hms"],
    queryFn: () => api.get<HmsFault[]>(`/printers/${printerId}/hms`),
    enabled,
    refetchInterval: 10_000,
  });
}

export function useHmsClear(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>(`/printers/${printerId}/hms/clear`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["printers", printerId, "hms"] }),
  });
}

export function useHmsAction(printerId: number) {
  return useMutation({
    mutationFn: (b: { print_error: string; action: string }) =>
      api.post<void>(`/printers/${printerId}/hms/action`, b),
  });
}

export function useFirmware(printerId: number, enabled = true) {
  return useQuery({
    queryKey: ["printers", printerId, "firmware"],
    queryFn: () => api.get<FirmwareInfo>(`/printers/${printerId}/firmware`),
    enabled,
  });
}

export function useFirmwareUpload(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<unknown>(`/printers/${printerId}/firmware/upload`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["printers", printerId, "firmware"] }),
  });
}

export function useDiagnostic() {
  return useMutation({
    mutationFn: (b: { printerId: number } | { adhoc: DiagnosticIn }) =>
      "adhoc" in b
        ? api.post<DiagnosticReport>("/printers/diagnostic", b.adhoc)
        : api.get<DiagnosticReport>(`/printers/${b.printerId}/diagnostic`),
  });
}

export function useDiscover() {
  return useMutation({
    mutationFn: (cidr: string | null) =>
      api.post<DiscoveredPrinter[]>("/printers/discover", cidr ? { cidr } : {}),
  });
}
