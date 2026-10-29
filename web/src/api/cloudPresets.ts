/**
 * Hooks for cloud preset sync (backend `app.api.cloud_presets`): pair Orca
 * Cloud, list a cloud account's presets with their sync status, diff one,
 * and import/resync them into the local slicer profiles. Bambu Cloud uses
 * the Bambu account connected in Settings.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

/** Shared by every cloud-preset mutation so the dialog can tell when one is in flight. */
export const CLOUD_MUTATION_KEY = ["cloud-presets-mutation"] as const;

export type CloudSource = "orca" | "bambu";
export type CloudPresetStatus =
  | "new"
  | "up_to_date"
  | "remote_changed"
  | "local_modified"
  | "conflict"
  | "removed";

export interface CloudSourceStatus {
  connected: boolean;
  account: string | null;
  needs_reconnect: boolean;
  pending: boolean;
}
export interface CloudStatus {
  orca: CloudSourceStatus;
  bambu: CloudSourceStatus;
}
export interface DeviceStart {
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string | null;
  expires_in: number;
  interval: number;
}
export interface DevicePoll {
  status: "pending" | "complete" | "denied" | "expired";
  interval: number;
}
export interface CloudPreset {
  preset_id: string;
  name: string;
  kind: "printer" | "process" | "filament";
  updated_time: string | null;
  status: CloudPresetStatus;
  profile_id: number | null;
}
export interface CloudPresetList {
  presets: CloudPreset[];
  removed: CloudPreset[];
}
export interface CloudDiff {
  preset_id: string;
  name: string;
  status: CloudPresetStatus;
  profile_id: number | null;
  changes: { key: string; local: unknown; remote: unknown }[];
}
export interface CloudSyncResult {
  created: number;
  updated: number;
  skipped: { preset_id: string; name: string; reason: string }[];
  failed: { preset_id: string; name: string; error: string }[];
}

const STATUS_KEY = ["cloud-presets", "status"] as const;

export function useCloudStatus(enabled = true) {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => api.get<CloudStatus>("/cloud-presets/status"),
    enabled,
  });
}

export function useOrcaDeviceStart() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: CLOUD_MUTATION_KEY,
    mutationFn: () => api.post<DeviceStart>("/cloud-presets/orca/device/start"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: STATUS_KEY }),
  });
}

export function useOrcaDevicePoll() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: CLOUD_MUTATION_KEY,
    mutationFn: () => api.post<DevicePoll>("/cloud-presets/orca/device/poll"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: STATUS_KEY }),
  });
}

export function useOrcaDisconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: CLOUD_MUTATION_KEY,
    mutationFn: () => api.delete<void>("/cloud-presets/orca"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["cloud-presets"] }),
  });
}

export function useCloudPresets(source: CloudSource, enabled: boolean) {
  return useQuery({
    queryKey: ["cloud-presets", source, "list"] as const,
    queryFn: () => api.get<CloudPresetList>(`/cloud-presets/${source}/presets`),
    enabled,
    retry: false,
  });
}

export function useCloudDiff(source: CloudSource, presetId: string | null) {
  return useQuery({
    queryKey: ["cloud-presets", source, "diff", presetId] as const,
    queryFn: () =>
      api.get<CloudDiff>(`/cloud-presets/${source}/presets/${encodeURIComponent(presetId ?? "")}/diff`),
    enabled: presetId != null,
    retry: false,
  });
}

export function useCloudSync(source: CloudSource) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: CLOUD_MUTATION_KEY,
    mutationFn: (body: { preset_ids?: string[]; overwrite_local?: boolean }) =>
      api.post<CloudSyncResult>(`/cloud-presets/${source}/sync`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cloud-presets", source] });
      void qc.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}
