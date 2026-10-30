/** Admin log viewer + MQTT relay + raw MQTT debug feed. */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export type LogLevelName = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface LogEntry {
  id: number;
  ts: number;
  level: string;
  logger: string;
  service: string;
  msg: string;
}

export interface LogsOut {
  entries: LogEntry[];
  last_id: number;
  services: string[];
  capacity: number;
}

export interface LogFilter {
  service: string;
  level: string;
  q: string;
}

export function logsQuery(f: LogFilter, extra: { after?: number; limit?: number } = {}): string {
  const p = new URLSearchParams();
  if (f.service) p.set("service", f.service);
  if (f.level) p.set("level", f.level);
  if (f.q.trim()) p.set("q", f.q.trim());
  if (extra.after != null) p.set("after", String(extra.after));
  if (extra.limit != null) p.set("limit", String(extra.limit));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const fetchLogs = (f: LogFilter, extra: { after?: number; limit?: number } = {}) =>
  api.get<LogsOut>(`/admin/logs${logsQuery(f, extra)}`);

export const logsDownloadUrl = (f: LogFilter) => `/api/admin/logs/download${logsQuery(f)}`;

// --- raw MQTT debug ---

export interface MqttDebugEntry {
  ts: number;
  dir: "in" | "cmd" | string;
  topic: string;
  payload: unknown;
}

export const fetchMqttHistory = (printerId: number, limit = 50) =>
  api.get<MqttDebugEntry[]>(`/admin/printers/${printerId}/mqtt?limit=${limit}`);

export const mqttStreamUrl = (printerId: number) => `/api/admin/printers/${printerId}/mqtt/stream`;

// --- generic MQTT relay ---

export interface RelayStatus {
  enabled: boolean;
  connected: boolean;
  error: string | null;
  actor_ok: boolean;
  actor_error: string | null;
  published: number;
  at: number;
}

export interface MqttRelay {
  enabled: boolean;
  host: string | null;
  port: number;
  tls: boolean;
  tls_verify: boolean;
  username: string | null;
  has_password: boolean;
  topic_prefix: string;
  commands_enabled: boolean;
  actor_user_id: number | null;
  low_filament_pct: number;
  actor_username: string | null;
  actor_ok: boolean;
  actor_error: string | null;
  status: RelayStatus | null;
}

export interface MqttRelayIn {
  enabled?: boolean;
  host?: string | null;
  port?: number;
  tls?: boolean;
  tls_verify?: boolean;
  username?: string | null;
  /** "***" or omitted keeps, "" clears. */
  password?: string | null;
  topic_prefix?: string;
  commands_enabled?: boolean;
  actor_user_id?: number | null;
  low_filament_pct?: number;
}

export const relayQueryOptions = queryOptions({
  queryKey: ["settings", "mqtt-relay"] as const,
  queryFn: () => api.get<MqttRelay>("/settings/mqtt-relay"),
  refetchInterval: 15_000,
});

export function useRelay() {
  return useQuery(relayQueryOptions);
}

export function useRelayActors() {
  return useQuery({
    queryKey: ["settings", "mqtt-relay", "actors"] as const,
    queryFn: () => api.get<{ id: number; username: string }[]>("/settings/mqtt-relay/actors"),
  });
}

export function useSaveRelay() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MqttRelayIn) => api.put<MqttRelay>("/settings/mqtt-relay", body),
    onSuccess: (data) => {
      qc.setQueryData(relayQueryOptions.queryKey, data);
      void qc.invalidateQueries({ queryKey: ["admin", "status"] });
    },
  });
}

export function useTestRelay() {
  return useMutation({
    mutationFn: (body: MqttRelayIn) =>
      api.post<{ ok: boolean; detail: string }>("/settings/mqtt-relay/test", body),
  });
}
