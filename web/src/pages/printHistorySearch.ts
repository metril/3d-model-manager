/**
 * `/print/history` route's URL contract (P3 Task 5): which sub-view the
 * temporary combined page shows. `tab=stats` is the old `/dashboard`
 * route's redirect target (library stats, `DashboardPage`'s content);
 * `tab=runs` (the default) shows the print-job history list
 * (`PrintJobHistory`). Kept in its own module -- no React export -- for the
 * same reason as `librarySearch.ts`/`viewerWindowSearch.ts`:
 * `validateSearch` has to serve as both the route's option and an
 * importable parser for the page, without tripping the
 * `react(only-export-components)` fast-refresh lint rule.
 */
export interface PrintHistorySearch {
  tab: "runs" | "stats" | "calendar";
  /** YYYY-MM-DD: Runs tab filtered to that (UTC) day, set by the calendar. */
  date?: string;
}

export function parsePrintHistorySearch(search: Record<string, unknown>): PrintHistorySearch {
  const tab = search.tab === "stats" || search.tab === "calendar" ? search.tab : "runs";
  const date = typeof search.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(search.date) ? search.date : undefined;
  return date ? { tab, date } : { tab };
}
