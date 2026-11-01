import type { TagCounts, TagOut } from "@/api/types";

export type SortKey = "name" | keyof TagCounts;
export type SortDir = "asc" | "desc";

export const COUNT_COLUMNS = ["total", "models", "model_files", "projects", "attachments", "archives", "collections"] as const;

export const totalOf = (tag: TagOut): number => tag.counts?.total ?? 0;

/** Case-insensitive name+description search, optional unused-only, then sort
 * (count ties break on name ascending). */
export function filterAndSort(
  tags: TagOut[],
  opts: { query: string; unusedOnly: boolean; sort: SortKey; dir: SortDir },
): TagOut[] {
  const needle = opts.query.trim().toLowerCase();
  const out = tags.filter(
    (tag) =>
      (!opts.unusedOnly || totalOf(tag) === 0) &&
      (!needle || tag.name.toLowerCase().includes(needle) || (tag.description ?? "").toLowerCase().includes(needle)),
  );
  const sign = opts.dir === "asc" ? 1 : -1;
  const byName = (a: TagOut, b: TagOut) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  out.sort((a, b) => {
    if (opts.sort === "name") return sign * byName(a, b);
    const diff = (a.counts?.[opts.sort] ?? 0) - (b.counts?.[opts.sort] ?? 0);
    return diff !== 0 ? sign * diff : byName(a, b);
  });
  return out;
}

export function plural(
  t: (key: string, opts?: Record<string, unknown>) => string,
  base: string,
  count: number,
  extra: Record<string, unknown> = {},
): string {
  return t(`${base}_${count === 1 ? "one" : "other"}`, { count, ...extra });
}
