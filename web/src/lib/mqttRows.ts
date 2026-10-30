import type { MqttDebugEntry } from "@/api/adminLogs";

export type MqttRow = MqttDebugEntry & { seq: number };

/** Tag entries with a monotonic seq so row keys never collide. */
export function withSeq(entries: MqttDebugEntry[], start: number): MqttRow[] {
  return entries.map((e, i) => ({ ...e, seq: start + i }));
}
