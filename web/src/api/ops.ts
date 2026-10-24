/** Q7 hooks: per-printer maintenance + smart plug, backups/ops settings, update check. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "@/api/client";
import type {
  BackupRun,
  MaintenanceLog,
  MaintenanceTask,
  MaintenanceTaskIn,
  OpsSettings,
  OpsSettingsIn,
  PlugIn,
  PlugOut,
  UpdateInfo,
} from "@/api/types";

const mKey = (pid: number) => ["printers", pid, "maintenance"] as const;
const plugKey = (pid: number) => ["printers", pid, "plug"] as const;
const plugSummaryKey = ["printers", "plugs", "summary"] as const;
const OPS = ["settings", "ops"] as const;
const BACKUPS = ["admin", "backups"] as const;

export function useMaintenanceTasks(pid: number) {
  return useQuery({ queryKey: mKey(pid), queryFn: () => api.get<MaintenanceTask[]>(`/printers/${pid}/maintenance`) });
}

export function useMaintenanceLogs(pid: number, taskId: number | null) {
  return useQuery({
    queryKey: [...mKey(pid), taskId, "logs"],
    queryFn: () => api.get<MaintenanceLog[]>(`/printers/${pid}/maintenance/${taskId}/logs`),
    enabled: taskId != null,
  });
}

function useInvalidate(pid: number) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: mKey(pid) });
  };
}

export function useSaveMaintenanceTask(pid: number) {
  const inv = useInvalidate(pid);
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: MaintenanceTaskIn }) =>
      id != null
        ? api.patch<MaintenanceTask>(`/printers/${pid}/maintenance/${id}`, body)
        : api.post<MaintenanceTask>(`/printers/${pid}/maintenance`, body),
    onSuccess: inv,
  });
}

export function useDeleteMaintenanceTask(pid: number) {
  const inv = useInvalidate(pid);
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/printers/${pid}/maintenance/${id}`),
    onSuccess: inv,
  });
}

export function useMarkMaintenanceDone(pid: number) {
  const inv = useInvalidate(pid);
  return useMutation({
    mutationFn: ({ id, note }: { id: number; note: string | null }) =>
      api.post<MaintenanceTask>(`/printers/${pid}/maintenance/${id}/done`, { note }),
    onSuccess: inv,
  });
}

export function useSetMaintenanceMode(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      api.put<{ maintenance_mode: boolean }>(`/printers/${pid}/maintenance/mode`, { enabled }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["printers"] });
    },
  });
}

/** Due count for the badge: tasks that are not "ok". */
export function dueCount(tasks: MaintenanceTask[] | undefined): number {
  return (tasks ?? []).filter((t) => t.status !== "ok").length;
}

/** 404 = no plug configured (a normal state), mapped to `null`. */
export function usePlug(pid: number, poll = true) {
  return useQuery({
    queryKey: plugKey(pid),
    queryFn: async () => {
      try {
        return await api.get<PlugOut>(`/printers/${pid}/plug`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    refetchInterval: poll ? 15_000 : false,
  });
}

/** Every visible printer's plug in ONE poll; absent id = no plug. */
export function usePlugSummary() {
  return useQuery({
    queryKey: plugSummaryKey,
    queryFn: () => api.get<{ plugs: Record<string, PlugOut> }>("/printers/plugs/summary"),
    refetchInterval: 15_000,
  });
}

export function usePlugToggle(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (on: boolean) => api.post<PlugOut>(`/printers/${pid}/plug/${on ? "on" : "off"}`),
    onSuccess: (d) => {
      qc.setQueryData(plugKey(pid), d);
      void qc.invalidateQueries({ queryKey: plugSummaryKey });
    },
  });
}

export function useSavePlug(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlugIn) => api.put<PlugOut>(`/printers/${pid}/plug`, body),
    onSuccess: (d) => {
      qc.setQueryData(plugKey(pid), d);
      void qc.invalidateQueries({ queryKey: plugSummaryKey });
    },
  });
}

export function useDeletePlug(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>(`/printers/${pid}/plug`),
    onSuccess: () => {
      qc.setQueryData(plugKey(pid), null);
      void qc.invalidateQueries({ queryKey: plugSummaryKey });
    },
  });
}

export function useOpsSettings() {
  return useQuery({ queryKey: OPS, queryFn: () => api.get<OpsSettings>("/settings/ops") });
}

export function useSaveOpsSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OpsSettingsIn) => api.put<OpsSettings>("/settings/ops", body),
    onSuccess: (d) => qc.setQueryData(OPS, d),
  });
}

export function useBackups(enabled = true) {
  return useQuery({
    queryKey: BACKUPS,
    queryFn: () => api.get<BackupRun[]>("/admin/backups"),
    enabled,
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running") ? 3000 : false),
  });
}

export function useRunBackup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<BackupRun>("/admin/backups/run"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: BACKUPS }),
  });
}

export function useRestoreSettings() {
  return useMutation({
    mutationFn: (id: number) => api.post<{ restored: number }>(`/admin/backups/${id}/restore-settings`),
  });
}

export function useUpdateInfo(enabled = true) {
  return useQuery({
    queryKey: ["system", "update"],
    queryFn: () => api.get<UpdateInfo>("/system/update"),
    enabled,
    staleTime: 10 * 60_000,
  });
}
