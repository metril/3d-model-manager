/**
 * `/inventory/profiles` URL contract: `?tab=`, `?printer=`, `?nozzle=`,
 * `?layer=`, `?q=`, `?all=1`. Defaults are `undefined` so a plain URL stays
 * clean. Kept free of React exports, like `printerDashSearch.ts`.
 */
export interface ProfilesSearch {
  /** Omitted for the default "profiles" tab. */
  tab?: "recipes" | "pipelines";
  printer?: number;
  nozzle?: number;
  layer?: number;
  q?: string;
  all?: 1;
}

function toNumber(raw: unknown): number {
  return typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() ? Number(raw) : NaN;
}

export function parseProfilesSearch(search: Record<string, unknown>): ProfilesSearch {
  const tab = search.tab === "recipes" || search.tab === "pipelines" ? search.tab : undefined;
  const printer = toNumber(search.printer);
  const nozzle = toNumber(search.nozzle);
  const layer = toNumber(search.layer);
  const q = typeof search.q === "string" && search.q.trim() ? search.q : undefined;
  return {
    tab,
    printer: Number.isInteger(printer) && printer > 0 ? printer : undefined,
    nozzle: Number.isFinite(nozzle) && nozzle > 0 ? nozzle : undefined,
    layer: Number.isFinite(layer) && layer > 0 ? layer : undefined,
    q,
    all: search.all === 1 || search.all === "1" || search.all === true || search.all === "true" ? 1 : undefined,
  };
}
