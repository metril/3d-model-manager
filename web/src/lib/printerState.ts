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
  // No report yet / unrecognised state is "no data", never a made-up "Idle".
  if (!state || state === "UNKNOWN") return { tone: "offline", label: "No data" };
  if (PRINTING_GCODE.includes(state)) return { tone: "printing", label: GCODE_LABELS[state] };
  if (PAUSED_GCODE.includes(state)) return { tone: "paused", label: GCODE_LABELS[state] };
  if (FINISHED_GCODE.includes(state)) return { tone: "finished", label: GCODE_LABELS[state] };
  if (FAILED_GCODE.includes(state)) return { tone: "failed", label: GCODE_LABELS[state] };
  return { tone: "idle", label: GCODE_LABELS[state] ?? "No data" };
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
  a2l: "a2l",
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

// Best-effort image fallback only (not authoritative).
// Bambu serial prefixes (first 3 chars) -> model key; used only when the name is unknown.
const SERIAL_PREFIX: Record<string, ModelKey> = {
  "00M": "x1c", "00W": "x1c", "03W": "x1e",
  "01S": "p1p", "01P": "p1s", "030": "a1mini", "039": "a1",
  "094": "h2d",
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

/** `value` is what gets stored; it must be a substring of the slicer's printer name
 * (e.g. "Bambu Lab X1 Carbon") because backend model matching is substring-based. */
export const PRINTER_MODELS: ReadonlyArray<{
  group: string;
  models: ReadonlyArray<{ value: string; label: string }>;
}> = [
  { group: "A1 series", models: [m("A1"), m("A1 mini"), m("A1F")] },
  { group: "A2 series", models: [m("A2L")] },
  { group: "H2 series", models: [m("H2C"), m("H2D"), m("H2D Pro"), m("H2S")] },
  { group: "O1 series", models: [m("O1C"), m("O1E"), m("O1S")] },
  { group: "P series", models: [m("P1P"), m("P1S"), m("P2S")] },
  { group: "X1 series", models: [{ value: "X1 Carbon", label: "X1C" }, m("X1E")] },
  { group: "X2 series", models: [m("X2D")] },
];

function m(v: string) {
  return { value: v, label: v };
}
