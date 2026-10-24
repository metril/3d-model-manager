/** Pure panel metadata (no React components) so the layout store and its
 * tests don't drag every card into the module graph. */
export const PANEL_IDS = [
  "viewer",
  "description",
  "tagsLinks",
  "related",
  "gcodeProfiles",
  "printHistory",
  "filesDocs",
  "revisions",
  "notes",
  "printTips",
  "specs",
] as const;

export type PanelId = (typeof PANEL_IDS)[number];

export const PANEL_TITLES: Record<PanelId, string> = {
  viewer: "3D viewer",
  description: "Description",
  tagsLinks: "Tags & links",
  related: "Related",
  gcodeProfiles: "G-code profiles",
  printHistory: "Print history",
  filesDocs: "Files",
  revisions: "Revisions",
  notes: "Notes",
  printTips: "Print tips",
  specs: "Specs",
};

/** Today's two-column order: elastic left column, then the right card stack. */
export const LEFT_PANELS: readonly PanelId[] = ["viewer", "description", "tagsLinks", "related"];
export const RIGHT_PANELS: readonly PanelId[] = [
  "gcodeProfiles",
  "printHistory",
  "filesDocs",
  "revisions",
  "notes",
  "printTips",
  "specs",
];

export function isPanelId(value: unknown): value is PanelId {
  return typeof value === "string" && (PANEL_IDS as readonly string[]).includes(value);
}
