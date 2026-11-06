import type { FileOut } from "@/api/types";
/**
 * The pop-out viewer window's URL contract. Kept in its own module (no React
 * component export) so `parseViewerWindowSearch` can serve as the route's
 * `validateSearch` AND be imported by the page, without tripping the
 * `react(only-export-components)` fast-refresh lint rule.
 */
export interface WindowSearch {
  ids?: string;
  bg?: string;
  bgc?: string;
  light?: string;
  colors?: string;
  /** `"1"`/`"0"` -> `tools.grid` (Task 6; always present in links minted by
   * `openInWindow`, see that function's comment). */
  grid?: string;
  /** `"1"` -> `tools.shading = "wireframe"` (Task 6; omitted at the
   * `"solid"` default). */
  wf?: string;
  /** `"1"` -> `tools.shading = "xray"` (R10; omitted at the `"solid"`
   * default). Mutually exclusive with `wf` by construction -- `openInWindow`
   * never sets both. */
  xr?: string;
  /** `"1"` -> `tools.autoRotate` (Task 6; omitted at the `false` default). */
  rot?: string;
  /** `"o"` -> `tools.ortho` (Task 6; omitted at the `false` default). */
  cam?: string;
  /** `"<axis>:<t>"`, e.g. `"x:0.35"` -> `tools.section` (Task 6; omitted
   * when the section is disabled). */
  sec?: string;
  /** 2-decimal `tools.explode`, e.g. `"0.40"` (Task 6; omitted at the `0`
   * default). */
  ex?: string;
  /** Profile blob hash (or `*` = all files) the opener showed. */
  blob?: string;
  /** Selected plate index within that profile. */
  plate?: string;
}

/** Validate + normalize the window's search params. TanStack's default parser
 * runs `JSON.parse` on each value, so a single numeric id (`?ids=13`) arrives
 * as the NUMBER 13, not "13" -- coerce primitives back to string so both
 * single-part and multi-part (`?ids=13,19`, already a non-numeric string)
 * links survive. The same thing happens to the Task 6 tools params that
 * happen to look like JSON numbers (`grid=0`/`grid=1`, `wf=1`, `rot=1`,
 * `ex=0.40`) -- `cam=o` and `sec=x:0.35` aren't valid JSON so they already
 * arrive as strings. Anything that isn't a string or number (objects,
 * arrays, null) drops to undefined. Everything downstream treats these as
 * strings. */
export function parseViewerWindowSearch(search: Record<string, unknown>): WindowSearch {
  const asString = (value: unknown): string | undefined =>
    typeof value === "string" ? value : typeof value === "number" ? String(value) : undefined;
  return {
    ids: asString(search.ids),
    bg: asString(search.bg),
    bgc: asString(search.bgc),
    light: asString(search.light),
    colors: asString(search.colors),
    grid: asString(search.grid),
    wf: asString(search.wf),
    xr: asString(search.xr),
    rot: asString(search.rot),
    cam: asString(search.cam),
    sec: asString(search.sec),
    ex: asString(search.ex),
    blob: asString(search.blob),
    plate: asString(search.plate),
  };
}

/** `?plate=` only counts as an integer >= 1 that exists on the requested profile. */
export function parsePlate(raw: string | number | undefined, blob: string | undefined, files: FileOut[]): number | undefined {
  if (raw === undefined || raw === "" || !blob) return undefined;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) return undefined;
  const exists = files.some((f) => f.blob_hash === blob && (f.meta?.plates ?? []).some((p) => p.index === n));
  return exists ? n : undefined;
}
