/**
 * Shared state-tone vocabulary for the printers overview dashboard (P3
 * Task 3/4): maps a printer's live `gcode_state` (M4's status contract) or
 * an archived run's terminal `result` onto one of a handful of named
 * tones, each backed by a `--state-*` CSS token (index.css, Task 4) so the
 * overview's badges/sparklines/tiles share one palette instead of each
 * picking colors ad hoc.
 */
import type { ArchiveResult } from "@/api/types";

export type StateTone = "printing" | "finished" | "failed" | "paused" | "idle" | "offline";

// Mirrors `printers.ts`'s ACTIVE_GCODE/TERMINAL_GCODE groupings, split out
// by the tone each sub-state maps to.
const PRINTING_GCODE = ["RUNNING", "PREPARE"];
const PAUSED_GCODE = ["PAUSE"];
const FINISHED_GCODE = ["FINISH"];
const FAILED_GCODE = ["FAILED"];

const GCODE_LABELS: Record<string, string> = {
  RUNNING: "Printing",
  PREPARE: "Preparing",
  PAUSE: "Paused",
  FINISH: "Finished",
  FAILED: "Failed",
  IDLE: "Idle",
};

/** Tone + human label for a printer's current `gcode_state`, folding in
 * `online` first -- an offline printer's last-known `gcode_state` is stale
 * and shouldn't read as "printing". */
export function toneForGcode(online: boolean, gcode: string | null): { tone: StateTone; label: string } {
  if (!online) return { tone: "offline", label: "Offline" };
  const state = gcode ?? "";
  if (PRINTING_GCODE.includes(state)) return { tone: "printing", label: GCODE_LABELS[state] };
  if (PAUSED_GCODE.includes(state)) return { tone: "paused", label: GCODE_LABELS[state] };
  if (FINISHED_GCODE.includes(state)) return { tone: "finished", label: GCODE_LABELS[state] };
  if (FAILED_GCODE.includes(state)) return { tone: "failed", label: GCODE_LABELS[state] };
  return { tone: "idle", label: GCODE_LABELS[state] ?? "Idle" };
}

/** Tone for an archive's terminal `result` (P2's `ArchiveResult`) -- only
 * `completed`/`failed` get their own tone; `canceled`/`stopped` read as a
 * neutral `idle` rather than implying success or failure. */
export function toneForResult(result: ArchiveResult): StateTone {
  if (result === "completed") return "finished";
  if (result === "failed") return "failed";
  return "idle";
}
