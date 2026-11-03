export type DateGroup = "Today" | "Yesterday" | "This week" | "This month" | "Older";

const DAY_MS = 86_400_000;

/** Calendar-day bucket for a timestamp relative to `now` (local time). Future
 * or unparseable dates land in "Today"/"Older" respectively. */
export function dateGroupOf(iso: string, now: Date = new Date()): DateGroup {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "Older";
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const ts = t.getTime();
  if (ts >= startOfToday) return "Today";
  if (ts >= startOfToday - DAY_MS) return "Yesterday";
  if (ts >= startOfToday - 6 * DAY_MS) return "This week";
  if (ts >= startOfToday - 29 * DAY_MS) return "This month";
  return "Older";
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
