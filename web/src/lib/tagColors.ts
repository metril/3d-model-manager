import type { CSSProperties } from "react";

import type { TagColor } from "@/api/types";

// -- user tags: free `#rrggbb` hex colors ---------------------------------

/** Quick-pick swatches for the tag color picker -- the original 10 palette
 * keys, as the hex values the backend migrated them to. */
export const TAG_PALETTE: readonly { key: TagColor; hex: string }[] = [
  { key: "slate", hex: "#64748b" },
  { key: "red", hex: "#ef4444" },
  { key: "orange", hex: "#f97316" },
  { key: "amber", hex: "#f59e0b" },
  { key: "green", hex: "#22c55e" },
  { key: "teal", hex: "#14b8a6" },
  { key: "blue", hex: "#3b82f6" },
  { key: "indigo", hex: "#6366f1" },
  { key: "violet", hex: "#8b5cf6" },
  { key: "pink", hex: "#ec4899" },
];

export const DEFAULT_TAG_COLOR = "#64748b";

const HEX_RE = /^#[0-9a-f]{6}$/i;

export function isHexColor(value: string | null | undefined): value is string {
  return !!value && HEX_RE.test(value);
}

/** Accepts `#abc`, `abc`, `#aabbcc`, `aabbcc`; returns lowercase `#rrggbb` or null. */
export function normalizeHex(input: string): string | null {
  let v = input.trim().replace(/^#/, "").toLowerCase();
  if (/^[0-9a-f]{3}$/.test(v)) v = v.replace(/./g, (c) => c + c);
  return /^[0-9a-f]{6}$/.test(v) ? `#${v}` : null;
}

function channel(c: number): number {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance of a `#rrggbb` color. */
export function relativeLuminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

/** WCAG contrast ratio (1..21) between two `#rrggbb` colors. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Black or white, whichever reads better on a SOLID fill of `hex` (swatch
 * check marks, icons on a filled dot). */
export function readableTextColor(hex: string): "#000000" | "#ffffff" {
  return contrastRatio(hex, "#000000") >= contrastRatio(hex, "#ffffff") ? "#000000" : "#ffffff";
}

/** Inline style for a tag chip: a translucent tint of the color for the
 * background/border, and text mixed toward the theme foreground so it
 * stays legible in both light and dark. Uncolored tags get no style (the
 * badge's own secondary look). */
export function tagChipStyle(color: string | null | undefined): CSSProperties | undefined {
  if (!isHexColor(color)) return undefined;
  return {
    backgroundColor: `color-mix(in srgb, ${color} 16%, transparent)`,
    borderColor: `color-mix(in srgb, ${color} 42%, transparent)`,
    color: `color-mix(in srgb, ${color} 55%, var(--foreground))`,
  };
}

// -- categories: legacy 10-key palette (still keyed by name) ---------------

/**
 * Fixed 10-key palette for user tags (R11-C item 16). Keys mirror the
 * backend's `TagColor` Literal (`app/schemas/library.py`) 1:1 -- a tag's
 * `color` is always one of these or `null`/`undefined` (uncolored, renders
 * with the default "secondary" badge look, no class from here).
 *
 * Each entry is a full Tailwind class string (not composed piecewise) so
 * the classes survive Tailwind's content-scan; light values first, `dark:`
 * variants alongside since this is consumed as a plain className, not
 * itself theme-aware.
 */
export const TAG_COLORS: readonly TagColor[] = [
  "slate",
  "red",
  "orange",
  "amber",
  "green",
  "teal",
  "blue",
  "indigo",
  "violet",
  "pink",
];

const TAG_COLOR_CLASSES: Record<TagColor, string> = {
  slate: "bg-slate-100 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300",
  red: "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300",
  orange: "bg-orange-100 text-orange-700 dark:bg-orange-500/20 dark:text-orange-300",
  amber: "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300",
  green: "bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-300",
  teal: "bg-teal-100 text-teal-700 dark:bg-teal-500/20 dark:text-teal-300",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300",
  indigo: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300",
  violet: "bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300",
  pink: "bg-pink-100 text-pink-700 dark:bg-pink-500/20 dark:text-pink-300",
};

/** Badge className for a tag color, or `undefined` for an uncolored tag
 * (falls back to the badge's own default "secondary" variant styling). */
export function tagColorClass(color: TagColor | null | undefined): string | undefined {
  return color ? TAG_COLOR_CLASSES[color] : undefined;
}

/** Small solid swatch className, for the color-picker itself. */
export function tagSwatchClass(color: TagColor): string {
  const swatch: Record<TagColor, string> = {
    slate: "bg-slate-400",
    red: "bg-red-400",
    orange: "bg-orange-400",
    amber: "bg-amber-400",
    green: "bg-green-400",
    teal: "bg-teal-400",
    blue: "bg-blue-400",
    indigo: "bg-indigo-400",
    violet: "bg-violet-400",
    pink: "bg-pink-400",
  };
  return swatch[color];
}
