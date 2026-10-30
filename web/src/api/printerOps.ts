/** Q5 hooks: HMS, firmware, connection diagnostics, discovery. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { toastControlError } from "@/api/printerControl";
import type {
  DiagnosticIn,
  DiagnosticReport,
  DiscoveredPrinter,
  FileStorage,
  FirmwareInfo,
  HmsFault,
  RemoteFile,
  SlotConfigIn,
  SlotConfigOut,
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

// ---- T3 printer ops ----

function invalidateStatus(qc: ReturnType<typeof useQueryClient>, printerId: number) {
  void qc.invalidateQueries({ queryKey: ["printers", printerId, "status"] });
  void qc.invalidateQueries({ queryKey: ["printers", "overview"] });
}

export function useClearPlate(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ awaiting_plate_clear: boolean }>(`/printers/${printerId}/clear-plate`),
    onSuccess: () => invalidateStatus(qc, printerId),
    onError: toastControlError,
  });
}

/** `POST /printers/{id}/slots/{unit}/{slot}` -- configure filament on the slot (+ device when `apply_device`). */
export function useConfigureSlot(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { unit: number; slot: number; body: SlotConfigIn }) =>
      api.put<SlotConfigOut | unknown[]>(`/printers/${printerId}/slots/${v.unit}/${v.slot}`, v.body),
    onSuccess: () => invalidateStatus(qc, printerId),
    onError: toastControlError,
  });
}

export function useResetSlotDevice(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { unit: number; slot: number }) =>
      api.delete<void>(`/printers/${printerId}/slots/${v.unit}/${v.slot}/device`),
    onSuccess: () => invalidateStatus(qc, printerId),
    onError: toastControlError,
  });
}

/** Empty `label` clears (DELETE). */
export function useAmsLabel(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { amsId: number; label: string }) =>
      v.label.trim()
        ? api.put<unknown>(`/printers/${printerId}/ams/${v.amsId}/label`, { label: v.label.trim() })
        : api.delete<unknown>(`/printers/${printerId}/ams/${v.amsId}/label`),
    onSuccess: () => invalidateStatus(qc, printerId),
    onError: toastControlError,
  });
}

// ---- AMS slot backup / restore ----
export interface AmsBackup {
  taken_at: string;
  slots: Array<{ ams_id: number; tray_id: number; tray_type: string; color: string | null }>;
}
const amsBackupKey = (printerId: number) => ["printers", printerId, "ams-backup"] as const;

export function useAmsBackup(printerId: number, enabled = true) {
  return useQuery({
    queryKey: amsBackupKey(printerId),
    queryFn: () => api.get<AmsBackup | null>(`/printers/${printerId}/ams/backup`),
    enabled,
  });
}

export function useAmsBackupSave(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AmsBackup>(`/printers/${printerId}/ams/backup`),
    onSuccess: (data) => qc.setQueryData(amsBackupKey(printerId), data),
    onError: toastControlError,
  });
}

export function useAmsRestore(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ restored: number; failed: number; pending: number }>(`/printers/${printerId}/ams/restore`),
    onSuccess: () => invalidateStatus(qc, printerId),
    onError: toastControlError,
  });
}

// ---- SD card ----
const filesKey = (printerId: number) => ["printers", printerId, "files"] as const;

/** Tolerates a bare array or `{entries|files: [...]}`; derives `path`/`is_dir` when absent. */
function normalizeFiles(raw: unknown, dir: string): RemoteFile[] {
  const list = (Array.isArray(raw) ? raw : ((raw as { entries?: unknown[]; files?: unknown[] })?.entries ?? (raw as { files?: unknown[] })?.files ?? [])) as Array<Partial<RemoteFile>>;
  const base = dir.replace(/\/+$/, "");
  return list.map((f) => ({
    name: f.name ?? "",
    path: f.path ?? `${base}/${f.name ?? ""}`,
    size: f.size ?? null,
    mtime: f.mtime ?? null,
    is_dir: !!f.is_dir,
  }));
}

export function usePrinterFiles(printerId: number, path: string, enabled = true) {
  return useQuery({
    queryKey: [...filesKey(printerId), "list", path] as const,
    queryFn: async () =>
      normalizeFiles(await api.get<unknown>(`/printers/${printerId}/files?path=${encodeURIComponent(path)}`), path),
    enabled,
  });
}

export function useFileStorage(printerId: number, enabled = true) {
  return useQuery({
    queryKey: [...filesKey(printerId), "storage"] as const,
    queryFn: () => api.get<FileStorage>(`/printers/${printerId}/files/storage`),
    enabled,
  });
}

export function fileDownloadUrl(printerId: number, path: string): string {
  return `/api/printers/${printerId}/files/download?path=${encodeURIComponent(path)}`;
}

export function useDeletePrinterFile(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => api.delete<void>(`/printers/${printerId}/files?path=${encodeURIComponent(path)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: filesKey(printerId) }),
    onError: toastControlError,
  });
}

export function usePrintSdFile(printerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { path: string; plate?: number; use_ams?: boolean }) =>
      api.post<{ ok: boolean; job_id: number }>(`/printers/${printerId}/files/print`, { use_ams: true, ...v }),
    onSuccess: () => {
      invalidateStatus(qc, printerId);
      void qc.invalidateQueries({ queryKey: ["printers", printerId, "active-job"] });
    },
    onError: toastControlError,
  });
}

