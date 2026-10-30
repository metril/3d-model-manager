/** Pure panel metadata for the print-dashboard dock (no React components). */
export const PANEL_IDS = ["printers", "queue", "archives", "camera", "stats", "forecast"] as const;
export type PanelId = (typeof PANEL_IDS)[number];

export const PANEL_TITLES: Record<PanelId, string> = {
  printers: "Printers",
  queue: "Up next",
  archives: "Recent archives",
  camera: "Cameras",
  stats: "Stats",
  forecast: "Stock forecast",
};

/** Default dock groups: printers on the left (today's main column); the rail
 * (queue over archives) on the right; the extras tabbed under the printers. */
export const DEFAULT_GROUPS = {
  main: ["printers"],
  railTop: ["queue"],
  railBottom: ["archives"],
  extras: ["camera", "stats", "forecast"],
} as const satisfies Record<string, readonly PanelId[]>;
export type DefaultGroup = keyof typeof DEFAULT_GROUPS;

export function isPanelId(value: unknown): value is PanelId {
  return typeof value === "string" && (PANEL_IDS as readonly string[]).includes(value);
}
