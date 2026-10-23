/**
 * Print dashboard (`/`) URL contract (Q3): `?status=`, `?offline=hide`,
 * `?q=`, `?sort=`. Defaults are `undefined` so a plain `/` URL stays clean
 * and the library params (`parseLibrarySearch`) that share this route are
 * never polluted. Kept free of React exports, like `printHistorySearch.ts`.
 */
export const DASH_STATUSES = ["printing", "paused", "finished", "available", "problem", "offline"] as const;
export type DashStatus = (typeof DASH_STATUSES)[number];
export const DASH_SORTS = ["name", "status", "model", "eta"] as const;
export type DashSort = (typeof DASH_SORTS)[number];

export interface PrintDashSearch {
  status?: DashStatus;
  offline?: "hide";
  q?: string;
  sort?: DashSort;
}

export function parsePrintDashSearch(search: Record<string, unknown>): PrintDashSearch {
  const status = DASH_STATUSES.find((s) => s === search.status);
  const sort = DASH_SORTS.find((s) => s === search.sort);
  const q = typeof search.q === "string" && search.q.trim() ? search.q : undefined;
  return {
    status,
    offline: search.offline === "hide" ? "hide" : undefined,
    q,
    sort: sort && sort !== "name" ? sort : undefined,
  };
}
