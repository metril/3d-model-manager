/**
 * Q3 printer control client: one mutation hook for every
 * `POST /printers/{id}/control/*` route plus skip-objects and the
 * Developer-Mode warnings poll. Errors map to toasts: 409
 * `dev_mode_off|offline|cold_extrude|printing|not_printing`, 422 `unsupported|invalid`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, api } from "@/api/client";
import type { ActiveObjectsOut, DevModeWarning, PrintersOverviewOut } from "@/api/types";

export type ControlPath =
  | "temperature"
  | "fan"
  | "light"
  | "speed"
  | "home"
  | "jog"
  | "extrude"
  | "airduct"
  | "calibrate"
  | "hms/clear"
  | "filament";

const MESSAGES: Record<string, string> = {
  dev_mode_off: "Developer LAN mode is off on this printer, so it rejects commands. Enable it on the printer.",
  offline: "The printer is offline.",
  cold_extrude: "Heat the nozzle to at least 170 °C first.",
  printing: "Not available while printing.",
  not_printing: "Only available during a print.",
  unsupported: "This printer model does not support that.",
  objects_unknown: "No object list is stored for this print.",
};

/** Human message for a control failure; falls back to the server detail. */
export function controlErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code && MESSAGES[error.code]) return MESSAGES[error.code];
    return error.detail;
  }
  return "Printer command failed.";
}

export function toastControlError(error: unknown): void {
  toast.error(controlErrorMessage(error));
}

function invalidate(qc: ReturnType<typeof useQueryClient>, printerId: number) {
  void qc.invalidateQueries({ queryKey: ["printers", printerId, "status"] });
  void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
}

export function useControl(printerId: number) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({ path, body }: { path: ControlPath; body?: Record<string, unknown> }) =>
      api.post<{ ok?: boolean; status?: string }>(`/printers/${printerId}/control/${path}`, body ?? {}),
    onSuccess: () => invalidate(qc, printerId),
    onError: toastControlError,
  });
  /** Resolves true on success, false after the error toast. */
  async function send(path: ControlPath, body?: Record<string, unknown>): Promise<boolean> {
    try {
      await mutation.mutateAsync({ path, body });
      return true;
    } catch {
      return false;
    }
  }
  return { send, pending: mutation.isPending };
}

export function usePrinterObjects(printerId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["printers", printerId, "objects"] as const,
    queryFn: () => api.get<ActiveObjectsOut>(`/printers/${printerId}/objects`),
    enabled,
    staleTime: 30_000,
  });
}

export function useSkipObjects(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (objectIds: number[]) =>
      api.post(`/printers/${printerId}/skip-objects`, { object_ids: objectIds }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers", printerId, "objects"] });
      invalidate(qc, printerId);
    },
    onError: toastControlError,
  });
}

export function useDevModeWarnings(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["printers", "developer-mode-warnings"] as const,
    queryFn: () => api.get<DevModeWarning[]>("/printers/developer-mode-warnings"),
    refetchInterval: 60_000,
    enabled: options?.enabled ?? true,
  });
}

/** Names of online printers whose Developer Mode state is still unknown (`dev_mode === null`). Shares the
 * overview cache entry; polls at 60 s only when nothing else (the dashboard) polls it faster. */
export function useDevModeUnknown(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["printers", "overview"] as const,
    queryFn: () => api.get<PrintersOverviewOut>("/printers/overview"),
    refetchInterval: 60_000,
    enabled: options?.enabled ?? true,
    select: (data) =>
      data.printers
        .filter((p) => p.status.online && p.status.dev_mode === null)
        .map((p) => ({ printer_id: p.printer.id, name: p.printer.name })),
  });
}
