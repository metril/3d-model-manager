/** Stock forecast hooks (backend/app/api/spool_forecast.py). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export interface ForecastConfig {
  enabled: boolean;
  lead_time_days: number;
  safety_margin_value: number;
  safety_margin_unit: "days" | "g";
  include_queue: boolean;
  notify: boolean;
}

export type SkuStatus = "ok" | "reorder" | "break";

export interface ForecastSku {
  material: string;
  brand: string | null;
  color: string | null;
  spool_count: number;
  remaining_g: number;
  demand_g: number;
  daily_rate_g: number | null;
  days_remaining: number | null;
  days_until_reorder: number | null;
  reorder_point_g: number;
  lead_time_days: number;
  status: SkuStatus;
  snoozed: boolean;
}

export interface Forecast {
  config: ForecastConfig;
  skus: ForecastSku[];
  queue_demand: { material: string; grams: number }[];
  queue_shortfalls: { material: string; demand_g: number; stock_g: number }[];
  alert_count: number;
  generated_at: string;
}

export interface SkuPatch {
  material: string;
  brand?: string | null;
  color?: string | null;
  lead_time_days?: number;
  safety_margin_value?: number;
  safety_margin_unit?: "days" | "g";
  alerts_snoozed?: boolean;
}

const KEY = ["spools", "forecast"] as const;

export function useForecast() {
  return useQuery({ queryKey: KEY, queryFn: () => api.get<Forecast>("/spools/forecast"), staleTime: 60_000 });
}

export function useForecastConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<ForecastConfig>) => api.put<ForecastConfig>("/spools/forecast/config", patch),
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useForecastSku() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: SkuPatch) => api.put<void>("/spools/forecast/sku", patch),
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }),
  });
}
