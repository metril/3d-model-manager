/** V4 plate-empty detection: reference calibration, check-plate, camera diagnose. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export interface PlateReference {
  id: number;
  plate_type: string;
  label: string;
  created_at: string;
}

export interface PlateCheckResult {
  empty: boolean;
  score: number;
  threshold: number;
  needs_calibration: boolean;
  message: string;
  references: number;
  preview: string | null;
}

export interface CameraDiagnose {
  camera_kind: string;
  worker_status: string | null;
  frame_bytes: number | null;
  viewers: number;
  ok: boolean;
  hint: string;
}

export const PLATE_NOT_EMPTY = "plate not empty";

const refsKey = (printerId: number) => ["printers", printerId, "plate-references"] as const;

export function usePlateReferences(printerId: number, enabled = true) {
  return useQuery({
    queryKey: refsKey(printerId),
    queryFn: () => api.get<PlateReference[]>(`/printers/${printerId}/plate-references`),
    enabled,
  });
}

export function useCaptureReference(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { plate_type: string; label?: string }) =>
      api.post<PlateReference>(`/printers/${printerId}/plate-references`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: refsKey(printerId) }),
  });
}

export function useDeleteReference(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/printers/${printerId}/plate-references/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: refsKey(printerId) }),
  });
}

export function useCheckPlate(printerId: number) {
  return useMutation({
    mutationFn: (body: { plate_type?: string; threshold?: number }) =>
      api.post<PlateCheckResult>(`/printers/${printerId}/check-plate`, body),
  });
}

export function useDiagnoseCamera(printerId: number) {
  return useMutation({
    mutationFn: () => api.post<CameraDiagnose>(`/printers/${printerId}/camera/diagnose`),
  });
}

export function referenceImageUrl(printerId: number, refId: number): string {
  return `/api/printers/${printerId}/plate-references/${refId}/image`;
}
