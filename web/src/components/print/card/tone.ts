import type { CSSProperties } from "react";

import type { StateTone } from "@/lib/printerState";

/** Top accent border per state (full literal class names so Tailwind sees them). */
export const TONE_BORDER_T: Record<StateTone, string> = {
  printing: "border-t-state-printing",
  finished: "border-t-state-finished",
  failed: "border-t-state-failed",
  paused: "border-t-state-paused",
  idle: "border-t-state-idle",
  offline: "border-t-state-offline",
};

export const toneVar = (tone: StateTone) => `var(--state-${tone})`;

/** Pill: 14% state tint background, state-coloured text. */
export function pillStyle(tone: StateTone): CSSProperties {
  return { background: `color-mix(in oklab, ${toneVar(tone)} 14%, transparent)`, color: toneVar(tone) };
}

/** Progress track at 16% of the state colour. */
export function trackStyle(tone: StateTone): CSSProperties {
  return { background: `color-mix(in oklab, ${toneVar(tone)} 16%, transparent)` };
}

/** Fill in the state colour; striped while paused. */
export function fillStyle(tone: StateTone, pct: number): CSSProperties {
  const base: CSSProperties = { width: `${pct}%`, background: toneVar(tone) };
  if (tone !== "paused") return base;
  return {
    ...base,
    backgroundImage: "repeating-linear-gradient(45deg, rgb(255 255 255 / 0.35) 0 4px, transparent 4px 8px)",
  };
}
