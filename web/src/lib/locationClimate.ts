import type { LocationSensor } from "@/api/types";

/** A reading older than this is stale (HA `last_changed` only moves with the value). */
export const STALE_MS = 3 * 60 * 60 * 1000;

export interface Climate {
  humidity: number | null;
  temp: number | null;
  /** Newest `last_changed_at` among the humidity/temperature sensors, ISO or null. */
  updatedAt: string | null;
  stale: boolean;
  /** Humidity above the drying "fair" threshold, or a sensor's own alert is firing. */
  warn: boolean;
}

function num(s: LocationSensor): number | null {
  if (s.last_state == null || s.last_state === "" || s.last_state === "unknown" || s.last_state === "unavailable") return null;
  const n = Number(s.last_state);
  return Number.isFinite(n) ? n : null;
}

/** Humidity (+ temperature) summary of a location's HA sensors; null when it has neither. */
export function locationClimate(
  sensors: LocationSensor[] | undefined,
  fairThreshold: number | undefined,
  now: number = Date.now(),
): Climate | null {
  const hum = (sensors ?? []).filter((s) => s.kind === "humidity" || s.device_class === "humidity");
  const tmp = (sensors ?? []).filter((s) => s.kind === "temperature" || s.device_class === "temperature");
  if (hum.length === 0 && tmp.length === 0) return null;
  const humidity = hum.map(num).find((v) => v != null) ?? null;
  const temp = tmp.map(num).find((v) => v != null) ?? null;
  const stamps = [...hum, ...tmp].map((s) => (s.last_changed_at ? Date.parse(s.last_changed_at) : NaN)).filter((n) => !Number.isNaN(n));
  const newest = stamps.length ? Math.max(...stamps) : null;
  const stale = newest == null || now - newest > STALE_MS;
  const alerting = [...hum, ...tmp].some((s) => s.alerting);
  const over = humidity != null && fairThreshold != null && humidity > fairThreshold;
  return {
    humidity,
    temp,
    updatedAt: newest == null ? null : new Date(newest).toISOString(),
    stale,
    warn: alerting || over,
  };
}
