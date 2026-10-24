/** Admin "Server" settings (env -> UI): config, slicer test, live status. */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { featuresQueryOptions } from "@/api/features";

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";

export interface ServerConfig {
  public_url: string | null;
  max_upload_bytes: number;
  max_download_bytes: number;
  sse_heartbeat_s: number;
  log_level: LogLevel;
  slicer_enabled: boolean;
  slicer_url: string;
  slice_timeout_s: number;
  vp_bind: string;
  vp_port_ssdp: number;
  vp_port_mqtt: number;
  vp_port_ftp: number;
  vp_pasv_min: number;
  vp_pasv_max: number;
  vp_pasv_address: string | null;
}

export interface ServerSettingsOut {
  config: ServerConfig;
  seeded_from_env: Record<string, string>;
  ignored_env: string[];
  derived: { cookie_secure: boolean | null; request_origin: string };
}

export interface SlicerTestOut {
  ok: boolean;
  latency_ms: number | null;
  detail: string;
}

export type VpState = "idle" | "running" | "error" | "offline";

export interface ServerStatusOut {
  slicer: { ok: boolean; detail: string };
  slicer_healthy: boolean | null;
  obico: { ok: boolean; detail: string };
  virtual_printer: {
    state: VpState;
    vp_id: number | null;
    bind: string | null;
    ports: { ssdp: number; mqtt: number; ftp: number } | null;
    pasv: [number, number] | null;
    errors: string[];
    ts: string | null;
  };
}

export const serverSettingsQueryOptions = queryOptions({
  queryKey: ["settings", "server"] as const,
  queryFn: () => api.get<ServerSettingsOut>("/settings/server"),
});

export const serverStatusQueryOptions = queryOptions({
  queryKey: ["settings", "server", "status"] as const,
  queryFn: () => api.get<ServerStatusOut>("/settings/server/status"),
  refetchInterval: 15_000,
});

export function useServerSettings() {
  return useQuery(serverSettingsQueryOptions);
}

export function useServerStatus() {
  return useQuery(serverStatusQueryOptions);
}

export function useUpdateServerSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ServerConfig) => api.put<ServerSettingsOut>("/settings/server", body),
    onSuccess: (data) => {
      qc.setQueryData(serverSettingsQueryOptions.queryKey, data);
      void qc.invalidateQueries({ queryKey: serverStatusQueryOptions.queryKey });
      void qc.invalidateQueries({ queryKey: featuresQueryOptions.queryKey });
    },
  });
}

export function useTestSlicer() {
  return useMutation({
    mutationFn: (url?: string) =>
      api.post<SlicerTestOut>("/settings/server/slicer/test", url ? { url } : {}),
  });
}
