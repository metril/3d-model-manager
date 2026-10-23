import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

export interface SensorPoint {
  ts: string;
  nozzle: number | null;
  nozzle_target: number | null;
  bed: number | null;
  bed_target: number | null;
  chamber: number | null;
  fan_part: number | null;
}
export interface AmsPoint {
  ts: string;
  humidity: number | null;
  temp: number | null;
}

function from(hours: number): string {
  return new Date(Date.now() - hours * 3600_000).toISOString();
}

/** `GET /printers/{id}/sensors` -- downsampled heater history. */
export function usePrinterSensors(printerId: number, hours: number, enabled = true) {
  return useQuery({
    queryKey: ["printers", printerId, "sensors", hours] as const,
    enabled,
    queryFn: () =>
      api.get<{ step_s: number; points: SensorPoint[] }>(
        `/printers/${printerId}/sensors?from=${encodeURIComponent(from(hours))}`,
      ),
  });
}

/** `GET /printers/{id}/ams-history` -- humidity/temp of one AMS unit. */
export function useAmsHistory(printerId: number, unit: number, hours: number, enabled = true) {
  return useQuery({
    queryKey: ["printers", printerId, "ams-history", unit, hours] as const,
    enabled,
    queryFn: () =>
      api.get<{ unit: number; step_s: number; points: AmsPoint[] }>(
        `/printers/${printerId}/ams-history?unit=${unit}&from=${encodeURIComponent(from(hours))}`,
      ),
  });
}
