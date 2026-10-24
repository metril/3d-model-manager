/** Obico failure detection (Q10): global settings, per-printer toggle, events. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type DetectionAction = "none" | "notify" | "pause";

export interface ObicoSettings {
  url: string;
  enabled: boolean;
  interval_s: number;
  warn: number;
  pause: number;
  action: DetectionAction;
  callback_url: string;
}

export interface DetectionStatus {
  active: boolean;
  state: "safe" | "warn" | "pause" | null;
  score: number | null;
  smoothed: number | null;
  detection_paused: boolean;
}

export interface PrinterDetection {
  enabled: boolean;
  action: DetectionAction | null;
  effective_action: DetectionAction;
  global_enabled: boolean;
  status: DetectionStatus;
}

export interface DetectionEvent {
  id: number;
  print_job_id: number | null;
  score: number;
  smoothed: number;
  action: string;
  has_frame: boolean;
  created_at: string;
}

export const obicoKey = ["settings", "obico"] as const;
const printerKey = (id: number) => ["printers", id, "detection"] as const;

export function useObicoSettings() {
  return useQuery({ queryKey: obicoKey, queryFn: () => api.get<ObicoSettings>("/settings/obico") });
}

export function useSaveObicoSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ObicoSettings) => api.put<ObicoSettings>("/settings/obico", body),
    onSuccess: (data) => qc.setQueryData(obicoKey, data),
  });
}

export function useTestObico() {
  return useMutation({
    mutationFn: (url?: string) =>
      api.post<{ ok: boolean; detail: string }>("/settings/obico/test", url ? { url } : {}),
  });
}

export function usePrinterDetection(printerId: number, opts: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: printerKey(printerId),
    queryFn: () => api.get<PrinterDetection>(`/printers/${printerId}/detection`),
    refetchInterval: opts.poll ? 10_000 : false,
  });
}

/** Every printable printer's live detection status in ONE poll (dashboard cards share it). */
export function useDetectionSummary() {
  return useQuery({
    queryKey: ["printers", "detections", "summary"] as const,
    queryFn: () => api.get<{ printers: Record<string, DetectionStatus> }>("/printers/detections/summary"),
    refetchInterval: 10_000,
  });
}

export function useSavePrinterDetection(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { enabled: boolean; action: DetectionAction | null }) =>
      api.put<PrinterDetection>(`/printers/${printerId}/detection`, body),
    onSuccess: (data) => qc.setQueryData(printerKey(printerId), data),
  });
}

export function useTestPrinterDetection(printerId: number) {
  return useMutation({
    mutationFn: () => api.post<{ score: number }>(`/printers/${printerId}/detection/test`, {}),
  });
}

export function usePrinterDetections(printerId: number) {
  return useQuery({
    queryKey: [...printerKey(printerId), "events"] as const,
    queryFn: () =>
      api.get<{ status: DetectionStatus; events: DetectionEvent[] }>(
        `/printers/${printerId}/detections`,
      ),
  });
}
