/** T1 AMS drying hooks: start/stop, schedules, presets, settings. */
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import i18n from "@/i18n";

import { ApiError, api } from "@/api/client";
import { controlErrorMessage } from "@/api/printerControl";
import type {
  DryingPreset,
  DryingScheduleIn,
  DryingSettings,
  DryingStartIn,
  ScheduledDrying,
} from "@/api/types";


export function dryingErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code && ["screen_only", "unsupported", "printing"].includes(error.code)) return i18n.t(`drying.error.${error.code}`);
    if (error.code === "blocked") return error.detail || i18n.t("drying.error.blocked");
  }
  return controlErrorMessage(error);
}

export function toastDryingError(error: unknown): void {
  toast.error(dryingErrorMessage(error));
}

/** UTC ISO instant from a `datetime-local` value (local time); null when empty/invalid. */
export function startAfterIso(atTime: string): string | null {
  if (!atTime) return null;
  const d = new Date(atTime);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

const schedKey = (printerId: number) => ["printers", printerId, "drying", "schedules"] as const;

function invalidatePrinter(qc: ReturnType<typeof useQueryClient>, printerId: number) {
  void qc.invalidateQueries({ queryKey: ["printers", printerId, "status"] });
  void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
  void qc.invalidateQueries({ queryKey: schedKey(printerId) });
}

/** Start/stop an AMS drying run. `start`/`stop` resolve true on success, false after the error toast. */
export function useDrying(printerId: number) {
  const qc = useQueryClient();
  const start = useMutation({
    mutationFn: (b: DryingStartIn) => api.post<{ ok?: boolean; status?: string }>(`/printers/${printerId}/drying/start`, b),
    onSuccess: () => invalidatePrinter(qc, printerId),
    onError: toastDryingError,
  });
  const stop = useMutation({
    mutationFn: (amsId: number) => api.post<{ ok?: boolean }>(`/printers/${printerId}/drying/stop`, { ams_id: amsId }),
    onSuccess: () => invalidatePrinter(qc, printerId),
    onError: toastDryingError,
  });
  const schedule = useMutation({
    mutationFn: (b: DryingScheduleIn) => api.post<ScheduledDrying>(`/printers/${printerId}/drying/schedules`, b),
    onSuccess: () => invalidatePrinter(qc, printerId),
    onError: toastDryingError,
  });
  const run = <V,>(m: { mutateAsync: (v: V) => Promise<unknown> }) => async (v: V) => {
    try {
      await m.mutateAsync(v);
      return true;
    } catch {
      return false;
    }
  };
  return {
    start: run(start),
    stop: run(stop),
    schedule: run(schedule),
    pending: start.isPending || stop.isPending || schedule.isPending,
  };
}

export function useDryingSchedules(printerId: number, enabled = true) {
  return useQuery({
    queryKey: schedKey(printerId),
    queryFn: () => api.get<ScheduledDrying[]>(`/printers/${printerId}/drying/schedules`),
    enabled,
    refetchInterval: 30_000,
  });
}

/** Schedules across several printers (one request each). */
export function useAllDryingSchedules(printerIds: number[]) {
  const results = useQueries({
    queries: printerIds.map((id) => ({
      queryKey: schedKey(id),
      queryFn: () => api.get<ScheduledDrying[]>(`/printers/${id}/drying/schedules`),
      refetchInterval: 30_000,
    })),
  });
  return {
    data: results.flatMap((r) => r.data ?? []),
    isLoading: results.some((r) => r.isLoading),
  };
}

export function useCancelSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (s: ScheduledDrying) => api.delete<ScheduledDrying>(`/drying/schedules/${s.id}`),
    onSuccess: (_d, s) => void qc.invalidateQueries({ queryKey: schedKey(s.printer_id) }),
    onError: toastDryingError,
  });
}

export function useDryingPresets(enabled = true) {
  return useQuery({
    queryKey: ["drying", "presets"] as const,
    queryFn: () => api.get<{ presets: Record<string, DryingPreset> }>("/drying/presets"),
    enabled,
    staleTime: 60_000,
  });
}

export function useDryingSettings() {
  return useQuery({
    queryKey: ["settings", "drying"] as const,
    queryFn: () => api.get<DryingSettings>("/settings/drying"),
  });
}

export function useUpdateDryingSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: DryingSettings) => api.put<DryingSettings>("/settings/drying", b),
    onSuccess: (data) => {
      qc.setQueryData(["settings", "drying"], data);
      void qc.invalidateQueries({ queryKey: ["drying", "presets"] });
      toast.success(i18n.t("drying.settings.saved"));
    },
    onError: toastDryingError,
  });
}
