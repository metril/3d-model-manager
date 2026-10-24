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

/** Default dock groups. Tabs within a group keep today's card order; the
 * default layout stacks `leftTop` over `leftBottom` and `rightTop` over
 * `rightBottom`. */
export const DEFAULT_GROUPS = {
  leftTop: ["viewer"],
  leftBottom: ["description", "tagsLinks", "related"],
  rightTop: ["gcodeProfiles", "printHistory", "filesDocs"],
  rightBottom: ["revisions", "notes", "printTips", "specs"],
} as const satisfies Record<string, readonly PanelId[]>;
export type DefaultGroup = keyof typeof DEFAULT_GROUPS;
/** The tab that starts active in each default group. */
export const DEFAULT_ACTIVE: Record<DefaultGroup, PanelId> = {
  leftTop: "viewer",
  leftBottom: "description",
  rightTop: "filesDocs",
  rightBottom: "revisions",
};

export function isPanelId(value: unknown): value is PanelId {
  return typeof value === "string" && (PANEL_IDS as readonly string[]).includes(value);
}
