/**
 * Query/mutation hooks for the printers domain (M4 Task 8): printers CRUD,
 * the Developer-Mode test probe, the live status poll, print-job history,
 * and pause/resume/stop commands. Mirrors `settings.ts`'s shape; the status
 * and print-jobs polls stop once there's nothing active left to watch, same
 * principle as `jobs.ts`'s `useJob`.
 */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  DetectSerialIn,
  DetectSerialOut,
  PrinterCameraOut,
  PrinterCreate,
  PrinterOut,
  PrinterStatusOut,
  PrinterUpdate,
  PrintJobOut,
  PrintRequest,
  ProbeOut,
} from "@/api/types";

// `gcode_state` values that mean "actively doing something" -- poll fast
// while any of these hold, back off to a slow heartbeat otherwise.
const ACTIVE_GCODE = ["RUNNING", "PREPARE", "PAUSE"];
// Confirmed-terminal `gcode_state` values (M4 status contract): once the
// printer reports one of these the print has definitively ended, so stop
// polling entirely (Task 11 fold of the M4 backlog minor). Anything else
// -- including an empty/unrecognized state -- keeps the slow heartbeat
// rather than risking a stuck "last known status" from stopping on a
// merely transient report.
const TERMINAL_GCODE = ["FINISH", "FAILED", "IDLE"];
// `PrintJobOut.state` values that haven't reached a terminal state yet.
const ACTIVE_JOB = ["queued", "uploading", "starting", "printing", "paused"];

/** The `usePrinterStatus` poll cadence for a given `gcode_state`: fast while
 * actively printing, stopped (`false`) once confirmed-terminal, slow
 * heartbeat for anything else (incl. unknown/empty). Exported as a pure
 * function so the cadence policy is unit-testable without driving the hook. */
export function statusRefetchInterval(gcodeState: string | null | undefined): number | false {
  const state = gcodeState ?? "";
  if (ACTIVE_GCODE.includes(state)) return 2500;
  if (TERMINAL_GCODE.includes(state)) return false;
  return 8000;
}

export const printersQueryOptions = queryOptions({
  queryKey: ["printers"] as const,
  queryFn: () => api.get<PrinterOut[]>("/printers"),
});

export function usePrinters(options?: { enabled?: boolean }) {
  return useQuery({ ...printersQueryOptions, enabled: options?.enabled ?? true });
}

export function useCreatePrinter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: PrinterCreate) => api.post<PrinterOut>("/printers", b),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["printers"] }),
  });
}

export function useUpdatePrinter(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: PrinterUpdate) => api.patch<PrinterOut>(`/printers/${id}`, b),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["printers"] }),
  });
}

export function useDeletePrinter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/printers/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["printers"] }),
  });
}

export function useTestPrinter(id: number) {
  return useMutation({ mutationFn: () => api.post<ProbeOut>(`/printers/${id}/test`) });
}

/** Backs the printer form's "Detect" button (Round 8 T1): reads the serial
 * straight off the printer's TLS cert, no saved printer required. */
export function useDetectSerial() {
  return useMutation({
    mutationFn: (b: DetectSerialIn) => api.post<DetectSerialOut>("/printers/detect-serial", b),
  });
}

/** Polls `GET /printers/{id}/status`, speeding up while the printer is
 * actively running/preparing/paused, backing off to a slow heartbeat for
 * any other non-terminal (including unknown/transient) state, and
 * stopping entirely once `gcode_state` reaches a confirmed terminal state
 * (Task 11 fold of the M4 backlog minor -- mirrors `useJob`/
 * `usePrintJobs`'s terminal-state stop). */
export function usePrinterStatus(id: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["printers", id, "status"] as const,
    queryFn: () => api.get<PrinterStatusOut>(`/printers/${id}/status`),
    refetchInterval: (q) => statusRefetchInterval(q.state.data?.gcode_state),
    enabled: options?.enabled ?? true,
  });
}

export function usePrinterCommand(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cmd: "pause" | "resume" | "stop") => api.post<void>(`/printers/${id}/${cmd}`),
    // Return the promise so the mutation stays pending until the status
    // refetch lands; callers that hold optimistic UI state clear it in
    // onSettled without snapping back to the stale cached value first.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["printers", id, "status"] }),
  });
}

export function useTogglePrinterLight(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (on?: boolean) => api.post<void>(`/printers/${id}/light`, on !== undefined ? { on } : {}),
    // Return the promise so the mutation stays pending until the status
    // refetch lands; callers that hold optimistic UI state clear it in
    // onSettled without snapping back to the stale cached value first.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["printers", id, "status"] }),
  });
}

/** Polls `GET /print-jobs`, stopping once every job in the current page has
 * reached a terminal state (mirrors `useJob`'s terminal-state stop). */
export function usePrintJobs(printerId?: number) {
  return useQuery({
    queryKey: ["print-jobs", printerId ?? null] as const,
    queryFn: () => api.get<PrintJobOut[]>(`/print-jobs${printerId ? `?printer_id=${printerId}` : ""}`),
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => ACTIVE_JOB.includes(j.state)) ? 3000 : false),
  });
}

export function useStartPrint(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: PrintRequest) => api.post<PrintJobOut>(`/printers/${printerId}/print`, b),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["print-jobs"] }),
  });
}

export function usePrinterCamera(id: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["printers", id, "camera"] as const,
    queryFn: () => api.get<PrinterCameraOut>(`/printers/${id}/camera`),
    enabled: options?.enabled ?? true,
    staleTime: 30000,
  });
}

