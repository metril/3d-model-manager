/** Stats-page widget layout: order + column span per widget, plus the day
 * range. Persisted per user in localStorage (the backend has no per-user
 * preferences store). Pure helpers so the reorder logic is unit-testable. */
export const WIDGET_IDS = [
  "quick",
  "success",
  "accuracy",
  "failures",
  "heatmap",
  "records",
  "printers",
  "filament",
  "stock",
] as const;
export type WidgetId = (typeof WIDGET_IDS)[number];
export type WidgetSize = 1 | 2 | 3;
export interface WidgetLayout {
  id: WidgetId;
  size: WidgetSize;
}
export interface StatsPrefs {
  widgets: WidgetLayout[];
  days: number;
}

export const DAY_OPTIONS = [7, 30, 90, 365] as const;

const DEFAULT_SIZE: Record<WidgetId, WidgetSize> = {
  quick: 3,
  success: 1,
  accuracy: 1,
  failures: 1,
  heatmap: 3,
  records: 1,
  printers: 2,
  filament: 3,
  stock: 2,
};

export const DEFAULT_PREFS: StatsPrefs = {
  widgets: WIDGET_IDS.map((id) => ({ id, size: DEFAULT_SIZE[id] })),
  days: 30,
};

/** Drops unknown/duplicate widgets and appends any missing ones. */
export function normalize(raw: unknown): StatsPrefs {
  const r = (raw ?? {}) as Partial<StatsPrefs>;
  const seen = new Set<string>();
  const widgets: WidgetLayout[] = [];
  for (const w of Array.isArray(r.widgets) ? r.widgets : []) {
    if (!w || !(WIDGET_IDS as readonly string[]).includes(w.id) || seen.has(w.id)) continue;
    seen.add(w.id);
    widgets.push({ id: w.id, size: w.size === 1 || w.size === 2 || w.size === 3 ? w.size : DEFAULT_SIZE[w.id] });
  }
  for (const id of WIDGET_IDS) if (!seen.has(id)) widgets.push({ id, size: DEFAULT_SIZE[id] });
  const days = (DAY_OPTIONS as readonly number[]).includes(r.days as number) ? (r.days as number) : 30;
  return { widgets, days };
}

export function reorder(widgets: WidgetLayout[], activeId: string, overId: string): WidgetLayout[] {
  const from = widgets.findIndex((w) => w.id === activeId);
  const to = widgets.findIndex((w) => w.id === overId);
  if (from < 0 || to < 0 || from === to) return widgets;
  const next = [...widgets];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export function cycleSize(widgets: WidgetLayout[], id: string): WidgetLayout[] {
  return widgets.map((w) => (w.id === id ? { ...w, size: ((w.size % 3) + 1) as WidgetSize } : w));
}

const key = (userId: number | undefined) => `tdmm.stats.layout.${userId ?? "anon"}`;

export function loadPrefs(userId: number | undefined): StatsPrefs {
  try {
    const raw = localStorage.getItem(key(userId));
    return normalize(raw ? JSON.parse(raw) : null);
  } catch {
    return normalize(null);
  }
}

export function savePrefs(userId: number | undefined, prefs: StatsPrefs): void {
  try {
    localStorage.setItem(key(userId), JSON.stringify(prefs));
  } catch {
    /* storage unavailable: layout just isn't remembered */
  }
}
