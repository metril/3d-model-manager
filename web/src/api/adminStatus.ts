/** Unified admin service-health (GET /admin/status). */
import { queryOptions, useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

export type ServiceState = "ok" | "warn" | "down" | "off";

export interface ServiceStatus {
  ok: boolean;
  state: ServiceState;
  detail: string;
  version: string | null;
  checked_at: number;
}

export interface AdminStatusOut {
  overall: "ok" | "warn" | "down";
  checked_at: number;
  services: Record<string, ServiceStatus>;
}

export const adminStatusQueryOptions = queryOptions({
  queryKey: ["admin", "status"] as const,
  queryFn: () => api.get<AdminStatusOut>("/admin/status"),
  refetchInterval: 15_000,
});

export function useAdminStatus(enabled = true) {
  return useQuery({ ...adminStatusQueryOptions, enabled });
}
