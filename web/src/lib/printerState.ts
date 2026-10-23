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

export type ModelKey =
  | "a1" | "a1mini" | "a1f" | "a2l" | "h2c" | "h2d" | "h2dpro" | "h2s" | "o1c" | "o1e" | "o1s"
  | "p1p" | "p1s" | "p2s" | "x1c" | "x1e" | "x2d" | "default";

/** Normalised-name -> key. Names are lowercased with spaces/dashes/underscores removed. */
const MODEL_LOOKUP: Record<string, ModelKey> = {
  a1: "a1", n2s: "a1",
  a1mini: "a1mini", n1: "a1mini",
  a1f: "a1f",
  a2l: "a2l", n9: "a2l",
  h2c: "h2c",
  h2d: "h2d",
  h2dpro: "h2dpro",
  h2s: "h2s",
  o1c: "o1c", o1e: "o1e", o1s: "o1s",
  p1p: "p1p", p1s: "p1s", p2s: "p2s",
  x1: "x1c", x1c: "x1c", x1carbon: "x1c",
  x1e: "x1e",
  x2d: "x2d",
};

const IMAGE_FILE: Record<ModelKey, string> = {
  a1: "a1", a1mini: "a1mini", a1f: "a1f", a2l: "a2l",
  h2c: "h2c", h2d: "h2d", h2dpro: "h2dpro", h2s: "h2d",
  o1c: "o1c", o1e: "o1e", o1s: "o1s",
  p1p: "p1p", p1s: "p1s", p2s: "p1s",
  x1c: "x1c", x1e: "x1e", x2d: "x2d", default: "default",
};

// Bambu serial prefixes (first 3 chars) -> model key; used only when the name is unknown.
const SERIAL_PREFIX: Record<string, ModelKey> = {
  "00M": "x1c", "00W": "x1c", "03W": "x1e",
  "01S": "p1p", "01P": "p1s", "030": "a1mini", "039": "a1",
  "094": "h2d", "093": "h2s", "31B": "h2c", "22E": "p2s",
};

/** Normalise a free-text model name (and optional serial) to a known key. */
export function modelKey(model: string | null | undefined, serial?: string | null): ModelKey {
  const m = (model ?? "").toLowerCase().replace(/[\s_-]+/g, "").replace(/^bambulab/, "");
  if (m && MODEL_LOOKUP[m]) return MODEL_LOOKUP[m];
  const prefix = (serial ?? "").slice(0, 3).toUpperCase();
  return SERIAL_PREFIX[prefix] ?? "default";
}

export function printerImage(key: ModelKey): string {
  return `/img/printers/${IMAGE_FILE[key] ?? "default"}.png`;
}

export const PRINTER_MODELS: ReadonlyArray<{ group: string; models: readonly string[] }> = [
  { group: "A1 series", models: ["A1", "A1 mini", "A1F"] },
  { group: "A2 series", models: ["A2L"] },
  { group: "H2 series", models: ["H2C", "H2D", "H2D Pro", "H2S"] },
  { group: "P series", models: ["P1P", "P1S", "P2S"] },
  { group: "X1 series", models: ["X1C", "X1E"] },
  { group: "X2 series", models: ["X2D"] },
];
