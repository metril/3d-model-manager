/**
 * The library route's URL contract (collection provenance, Task 2; category
 * + folder-path, R13b). A model's provenance badge and its "More from
 * <collection>" strip both link to `/?collection=<id>` to hand the gallery
 * straight to that followed collection's models; the sidebar's Categories
 * list links to `/?category=<id>` the same way. `path` is the Folders view's
 * current directory (empty/undefined at the root). Kept in its own module
 * (no React export) for the same reason as `viewerWindowSearch.ts` --
 * `validateSearch` has to serve as the route's option AND be importable by
 * the page, without tripping the `react(only-export-components)` fast-refresh
 * lint rule.
 */
export interface LibrarySearch {
  collection?: number;
  category?: number;
  path?: string;
  /** Gallery filters/sort (round-tripped so Back from a detail page restores them). */
  q?: string;
  tags?: number[];
  tagMode?: "any" | "all";
  fileTags?: number[];
  fileTagMode?: "any" | "all";
  format?: string[];
  sliced?: boolean;
  fav?: boolean;
  archived?: boolean;
  sort?: string;
}

const numberList = (v: unknown): number[] | undefined => {
  const list = Array.isArray(v) ? v.filter((x): x is number => typeof x === "number") : typeof v === "number" ? [v] : [];
  return list.length ? list : undefined;
};
const stringList = (v: unknown): string[] | undefined => {
  const list = Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : typeof v === "string" ? [v] : [];
  return list.length ? list : undefined;
};
const mode = (v: unknown): "any" | "all" | undefined => (v === "any" || v === "all" ? v : undefined);
const flag = (v: unknown): boolean | undefined => (v === true ? true : undefined);

/** TanStack's default search parser runs `JSON.parse` on each value, so a
 * numeric `?collection=5` already arrives as the NUMBER 5 (see
 * `viewerWindowSearch.ts`'s comment for the mirror-image case, where a
 * numeric-looking param needs coercing back to a string). Here the shoe's on
 * the other foot: anything that ISN'T already a number -- a hand-edited link,
 * a non-numeric string, an object -- must not leak through as a bogus filter,
 * so it drops to undefined instead of being trusted. `path` is expected to
 * already be a plain string (folder names, not JSON-shaped), so anything else
 * drops the same way. */
export function parseLibrarySearch(search: Record<string, unknown>): LibrarySearch {
  const collection = search.collection;
  const category = search.category;
  const path = search.path;
  return {
    collection: typeof collection === "number" ? collection : undefined,
    category: typeof category === "number" ? category : undefined,
    path: typeof path === "string" ? path : undefined,
    // The default parser JSON.parses values, so a numeric-looking query
    // (`?q=123`) arrives as a number -- stringify it back.
    q: typeof search.q === "string" && search.q ? search.q : typeof search.q === "number" ? String(search.q) : undefined,
    tags: numberList(search.tags),
    tagMode: mode(search.tagMode),
    fileTags: numberList(search.fileTags),
    fileTagMode: mode(search.fileTagMode),
    format: stringList(search.format),
    sliced: flag(search.sliced),
    fav: flag(search.fav),
    archived: flag(search.archived),
    sort: typeof search.sort === "string" ? search.sort : undefined,
  };
}
