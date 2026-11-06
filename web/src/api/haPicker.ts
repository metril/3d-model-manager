/** Home Assistant entity / service pickers (`GET /ha/entities`, `/ha/services`).
 * Both need `settings:write` and fail with 503 while HA is unconfigured or
 * unreachable -- the combobox then falls back to free text. */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { HaEntity } from "@/api/types";

export interface HaEntityFilter {
  /** Entity domains, e.g. `["sensor"]`. Empty/omitted = any. */
  domain?: readonly string[];
  /** Device classes, e.g. `["power"]`. Empty/omitted = any. */
  deviceClass?: readonly string[];
}

export function useHaPickerEntities(filter: HaEntityFilter, enabled = true) {
  const domain = (filter.domain ?? []).join(",");
  const deviceClass = (filter.deviceClass ?? []).join(",");
  return useQuery({
    queryKey: ["ha", "entities", domain, deviceClass],
    queryFn: () => {
      const qs = new URLSearchParams();
      if (domain) qs.set("domain", domain);
      if (deviceClass) qs.set("device_class", deviceClass);
      return api.get<HaEntity[]>(`/ha/entities?${qs.toString()}`);
    },
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}

export function useHaServices(domain: string, enabled = true) {
  return useQuery({
    queryKey: ["ha", "services", domain],
    queryFn: () => api.get<string[]>(`/ha/services?domain=${encodeURIComponent(domain)}`),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}
