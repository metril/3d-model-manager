/**
 * Query/mutation hooks for the printers domain (M4 Task 8): printers CRUD,
 * the Developer-Mode test probe, the live status poll, print-job history,
 * and pause/resume/stop commands. Mirrors `settings.ts`'s shape; the status
 * and print-jobs polls stop once there's nothing active left to watch, same
 * principle as `jobs.ts`'s `useJob`.
 */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { toastControlError } from "@/api/printerControl";
import type {
  CameraStatusOut,
  CameraUrlsOut,
  DetectSerialIn,
  DetectSerialOut,
  OverlayTokenCreatedOut,
  OverlayTokenOut,
  PrinterCreate,
  PrinterOut,
  PrinterStatusOut,
  PrinterUpdate,
  PrintersOverviewOut,
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

/** The `usePrintersOverview` poll cadence (P3 Task 3): fast while any
 * printer in the payload is actively printing/preparing/paused, backing
 * off to a slow heartbeat otherwise -- mirrors `statusRefetchInterval`'s
 * policy but evaluated across every printer in the dashboard at once. */
export function overviewRefetchInterval(data?: PrintersOverviewOut): number {
  const active = data?.printers.some((p) => ACTIVE_GCODE.includes(p.status.gcode_state ?? ""));
  return active ? 2500 : 8000;
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
    // `["printers"]` prefix-matches `["printers", "overview"]` under
    // react-query's default partial matching, so this one invalidation
    // already covers the overview too.
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers"] });
    },
  });
}

export function useUpdatePrinter(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: PrinterUpdate) => api.patch<PrinterOut>(`/printers/${id}`, b),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers"] });
    },
  });
}

export function useDeletePrinter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/printers/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers"] });
    },
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
    onError: toastControlError,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers", id, "status"] });
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["print-jobs"] });
      void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
    },
  });
}

/** Polls `GET /printers/overview` (P3 Task 3): one-call dashboard payload
 * for every visible printer's live status + active job + recent history,
 * plus the queue's head and cross-printer recent runs. Cadence mirrors
 * `usePrinterStatus`'s active/idle split, evaluated across all printers. */
export function usePrintersOverview(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["printers", "overview"] as const,
    queryFn: () => api.get<PrintersOverviewOut>("/printers/overview"),
    refetchInterval: (q) => overviewRefetchInterval(q.state.data),
    enabled: options?.enabled ?? true,
  });
}

// The minted token's TTL (backend `signed_urls.DEFAULT_TTL_S`) is 600s.
// `staleTime` sits well under that so a long-open tab's remount/refocus
// refreshes the token before it expires, and `cameraTokenNeedsRefresh`
// forces a refetch right away once expiry is within this margin, even if
// `staleTime` hasn't elapsed yet.
const CAMERA_TOKEN_STALE_MS = 5 * 60_000;
const CAMERA_TOKEN_EXPIRY_MARGIN_MS = 60_000;

/** True once `urls.expires_at` is within `CAMERA_TOKEN_EXPIRY_MARGIN_MS`
 * of now (or already past) -- exported so it can be unit-tested directly. */
export function cameraTokenNeedsRefresh(urls: CameraUrlsOut | undefined): boolean {
  if (!urls) return false;
  return new Date(urls.expires_at).getTime() - Date.now() <= CAMERA_TOKEN_EXPIRY_MARGIN_MS;
}

/** Mints a scoped camera token (P4 Task 8): `POST /printers/{id}/camera/
 * token` returns short-lived stream/snapshot URLs. Cached under its own
 * `["camera-url", id]` key -- deliberately OUTSIDE the `["printers"]`
 * prefix so the overview/status SSE-driven invalidations never re-mint a
 * token mid-stream. `enabled` lets `CameraTile` skip the mint entirely
 * while the printer is offline. No retry: a failed mint should surface as
 * the offline state, not hammer the endpoint. `staleTime` is well under
 * the token's 600s TTL so a remount/refocus well into its life re-mints
 * it, and `refetchOnMount`/`refetchOnWindowFocus` force that refresh
 * immediately -- rather than waiting out `staleTime` -- once it's within
 * a minute of actually expiring. */
export function useCameraUrls(printerId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["camera-url", printerId] as const,
    queryFn: () => api.post<CameraUrlsOut>(`/printers/${printerId}/camera/token`),
    enabled: options?.enabled ?? true,
    staleTime: CAMERA_TOKEN_STALE_MS,
    refetchOnMount: (query) => (cameraTokenNeedsRefresh(query.state.data) ? "always" : true),
    refetchOnWindowFocus: (query) => (cameraTokenNeedsRefresh(query.state.data) ? "always" : true),
    retry: false,
  });
}

/** `GET /printers/{id}/camera/status`: source kind, rotation, beta flag and
 * default fps. Failure is non-fatal (the tile falls back to defaults). */
export function useCameraStatus(printerId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["camera-status", printerId] as const,
    queryFn: () => api.get<CameraStatusOut>(`/printers/${printerId}/camera/status`),
    enabled: options?.enabled ?? true,
    retry: false,
    staleTime: 30_000,
  });
}

export function useOverlayTokens(printerId: number) {
  return useQuery({
    queryKey: ["overlay-tokens", printerId] as const,
    queryFn: () => api.get<OverlayTokenOut[]>(`/printers/${printerId}/overlay-tokens`),
  });
}

export function useCreateOverlayToken(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (label?: string) =>
      api.post<OverlayTokenCreatedOut>(`/printers/${printerId}/overlay-tokens`, label ? { label } : {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["overlay-tokens", printerId] }),
  });
}

export function useRevokeOverlayToken(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tokenId: number) => api.delete<void>(`/printers/${printerId}/overlay-tokens/${tokenId}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["overlay-tokens", printerId] }),
  });
}

/** `POST /printers/{id}/control/light` (Q2 command bus). */
export function useCameraLight(printerId: number) {
  return useMutation({
    mutationFn: (on: boolean) => api.post<void>(`/printers/${printerId}/control/light`, { on }),
  });
}
