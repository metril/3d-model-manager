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
  tab: "runs" | "stats";
}

export function parsePrintHistorySearch(search: Record<string, unknown>): PrintHistorySearch {
  return { tab: search.tab === "stats" ? "stats" : "runs" };
}
