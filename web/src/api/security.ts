import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export interface SecuritySettings {
  require_2fa: boolean;
}

export const securitySettingsQueryOptions = queryOptions({
  queryKey: ["settings", "security"] as const,
  queryFn: () => api.get<SecuritySettings>("/settings/security"),
});

export function useSecuritySettings() {
  return useQuery(securitySettingsQueryOptions);
}

/** Admin: `PUT /settings/security`. */
export function useUpdateSecuritySettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SecuritySettings) => api.put<SecuritySettings>("/settings/security", body),
    onSuccess: (data) => qc.setQueryData(securitySettingsQueryOptions.queryKey, data),
  });
}
