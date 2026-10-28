/**
 * Pure, `chrome`-free URL detection for the three supported gallery sites.
 * Mirrors the backend's per-importer `canonicalize` regexes (see
 * `backend/app/importers/{makerworld,thingiverse,printables}.py`) so the
 * extension's "is this a model page" check agrees with what the app will
 * actually accept -- the backend still does the authoritative canonicalize;
 * this is only a gate for enabling the popup button / context menu.
 *
 * Every pattern is anchored and tested against the URL's PATHNAME only (never
 * the query string or hash), so a model-looking string in a query can't
 * false-positive. The one exception is Thingiverse's `?thing=<id>` form,
 * which is read from `searchParams` explicitly. MakerWorld hosts, the locale
 * prefix and the collection-detail parser are shared with `collections.js`
 * (single source).
 */

import { LOCALE_PREFIX, MAKERWORLD_HOSTS, parseCollectionDetailUrl } from "./collections.js";

const rx = (source) => new RegExp(source, "i");

const SITES = [
  {
    site: "makerworld",
    hosts: MAKERWORLD_HOSTS,
    pathPattern: rx(`^/${LOCALE_PREFIX}models/\\d+(?:[-/].*)?$`),
  },
  {
    site: "thingiverse",
    hosts: new Set(["thingiverse.com", "www.thingiverse.com"]),
    // `/thing:123`, `/thing:123/files`, `/en/thing:123`.
    pathPattern: rx(`^/${LOCALE_PREFIX}thing:\\d+(?:/.*)?$`),
    queryParam: "thing",
  },
  {
    site: "printables",
    hosts: new Set(["printables.com", "www.printables.com"]),
    pathPattern: rx(`^/${LOCALE_PREFIX}model/\\d+(?:[-/].*)?$`),
  },
];

// `/@<handle>/collections`, optionally locale-prefixed, optionally followed
// by a further path segment. Pathname-only and anchored at both ends.
const COLLECTIONS_PATH_PATTERN = rx(`^/${LOCALE_PREFIX}@[^/]+/collections(?:/.*)?$`);

/** Match patterns for the supported gallery sites (kept equal to manifest `host_permissions`). */
export const GALLERY_HOST_PATTERNS = [
  "https://makerworld.com/*",
  "https://www.makerworld.com/*",
  "https://thingiverse.com/*",
  "https://www.thingiverse.com/*",
  "https://printables.com/*",
  "https://www.printables.com/*",
];

/**
 * The one URL -> site resolver: `{entry, parsed}` for a supported gallery
 * host, else `null` (also for unparseable input).
 * @param {string} url
 */
export function siteEntryFor(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const host = (parsed.hostname || "").toLowerCase();
  const entry = SITES.find((s) => s.hosts.has(host));
  return entry ? { entry, parsed } : null;
}

/**
 * @param {string} url
 * @returns {"makerworld"|"thingiverse"|"printables"|null}
 */
export function detectSite(url) {
  return siteEntryFor(url)?.entry.site ?? null;
}

/**
 * True iff `url` is on a supported gallery host AND its pathname matches that
 * site's model pattern (or, for Thingiverse, carries a numeric `?thing=`).
 * @param {string} url
 * @returns {boolean}
 */
export function isModelPage(url) {
  const hit = siteEntryFor(url);
  if (!hit) {
    return false;
  }
  const { entry, parsed } = hit;
  if (entry.pathPattern.test(parsed.pathname)) {
    return true;
  }
  return Boolean(entry.queryParam && /^\d+$/.test(parsed.searchParams.get(entry.queryParam) ?? ""));
}

function onMakerworld(url) {
  return siteEntryFor(url)?.entry.site === "makerworld" ? siteEntryFor(url).parsed : null;
}

/**
 * True iff `url` is a MakerWorld user's collections page --
 * `/@<handle>/collections`, optionally locale-prefixed and/or with a
 * trailing path segment. Gates the popup's "Sync collections to app" button.
 * @param {string} url
 * @returns {boolean}
 */
export function isCollectionsPage(url) {
  const parsed = onMakerworld(url);
  return Boolean(parsed && COLLECTIONS_PATH_PATTERN.test(parsed.pathname));
}

/**
 * True iff `url` is a MakerWorld collection DETAIL page
 * (`/<locale>/collections/<id>[-slug]`, no `@handle`), per the shared
 * `parseCollectionDetailUrl`. Distinct from `isCollectionsPage`, which gates
 * the bulk LIST sync.
 * @param {string} url
 * @returns {boolean}
 */
export function isCollectionDetailPage(url) {
  return Boolean(onMakerworld(url) && parseCollectionDetailUrl(url));
}
