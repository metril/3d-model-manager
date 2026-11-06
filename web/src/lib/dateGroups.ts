export type DateGroup = "Today" | "Yesterday" | "This week" | "This month" | "Older";

import i18n from "@/i18n";

/** Calendar-day bucket for a timestamp relative to `now` (local time). Future
 * or unparseable dates land in "Today"/"Older" respectively. */
export function dateGroupOf(iso: string, now: Date = new Date()): DateGroup {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "Older";
  // Midnight n calendar days back, built from date parts so a DST change
  // (23h/25h day) can't shift a boundary by an hour.
  const startOf = (daysBack: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysBack).getTime();
  const ts = t.getTime();
  if (ts >= startOf(0)) return "Today";
  if (ts >= startOf(1)) return "Yesterday";
  if (ts >= startOf(6)) return "This week";
  if (ts >= startOf(29)) return "This month";
  return "Older";
}

/** Localised display label for a group. */
export function dateGroupLabel(group: DateGroup): string {
  return i18n.t(`dateGroups.${group}`);
}

/** Groups already newest-first rows into consecutive labelled buckets,
 * preserving order (a group appears once, at its first row). */
export function groupByDate<T>(
  rows: readonly T[],
  getDate: (row: T) => string,
  now: Date = new Date(),
): Array<{ label: DateGroup; rows: T[] }> {
  const groups: Array<{ label: DateGroup; rows: T[] }> = [];
  for (const row of rows) {
    const label = dateGroupOf(getDate(row), now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.rows.push(row);
    else groups.push({ label, rows: [row] });
  }
  return groups;
}
