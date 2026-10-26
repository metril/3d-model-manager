import type { MqttDebugEntry } from "@/api/adminLogs";

/** Does `value` contain `needle` as a key name (deep) or as a value substring? */
export function matchesFilter(entry: MqttDebugEntry, needle: string): boolean {
  const n = needle.trim().toLowerCase();
  if (!n) return true;
  if (entry.topic.toLowerCase().includes(n)) return true;
  const walk = (v: unknown): boolean => {
    if (v == null) return false;
    if (Array.isArray(v)) return v.some(walk);
    if (typeof v === "object") {
      return Object.entries(v as Record<string, unknown>).some(([k, x]) => k.toLowerCase().includes(n) || walk(x));
    }
    return String(v).toLowerCase().includes(n);
  };
  return walk(entry.payload);
}
