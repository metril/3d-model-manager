/** Word-prefix beats substring; loose subsequence matches (cmdk's default,
 * which surfaces "Duplicates" for "pla") are dropped. */
export function paletteFilter(value: string, search: string): number {
  const q = search.trim().toLowerCase();
  if (!q) return 1;
  const v = value.toLowerCase();
  if (v.startsWith(q)) return 1;
  if (v.split(/[^a-z0-9]+/).some((w) => w.startsWith(q))) return 0.8;
  return v.includes(q) ? 0.4 : 0;
}
