/**
 * Pure, `chrome`-free URL detection for the three supported gallery sites.
 * Mirrors the backend's per-importer `canonicalize` regexes (see
 * `backend/app/importers/{makerworld,thingiverse,printables}.py`) so the
 * extension's "is this a model page" check agrees with what the app will
 * actually accept — the backend still does the authoritative canonicalize;
 * this is only a loose gate for enabling the popup button / context menu.
 */

const SITES = [
  {
    site: "makerworld",
    hosts: new Set(["makerworld.com", "www.makerworld.com"]),
    idPattern: /models\/(\d+)/i,
  },
  {
    site: "thingiverse",
    hosts: new Set(["thingiverse.com", "www.thingiverse.com"]),
    idPattern: /(?:thing:|.*?[?&]thing=)(\d+)/i,
  },
  {
    site: "printables",
    hosts: new Set(["printables.com", "www.printables.com"]),
    idPattern: /model\/(\d+)/i,
  },
];

const MAKERWORLD_HOSTS = new Set(["makerworld.com", "www.makerworld.com"]);

// `/@<handle>/collections`, optionally locale-prefixed (`/en/@handle/...`)
// and optionally followed by a further path segment (viewing one collection)
// or a query string (the query is naturally ignored since it's not part of
// `URL#pathname`). Tested against the pathname only, anchored at both ends
// so an unrelated page that merely contains "/collections/" somewhere
// doesn't false-positive.
const COLLECTIONS_PATH_PATTERN = /^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?@[^/]+\/collections(?:\/.*)?$/i;

// GROUND TRUTH (a real logged-in browser, M11): a MakerWorld collection
// DETAIL page is `/{locale}/collections/{id}-{slug}`, e.g.
// `https://makerworld.com/en/collections/18925823-esp32` -- an entirely
// different route from `COLLECTIONS_PATH_PATTERN` above (no `@handle`
// segment, and requires a numeric id). Kept in sync with `collections.js`'s
// `COLLECTION_DETAIL_PATH_RE` (same shape, duplicated locally rather than
// imported -- mirrors this file/`popup.js`/`background.js`'s already-
// duplicated `execInTab` pattern). Anchored so the bare index page
// (`/@handle/collections`, no id) never matches here. PLURAL-ONLY (M1
// hardening): this is the gate that decides whether the background
// auto-sync even LOOKS at a page as a collection detail page at all, so it
// must never tolerate the singular "collection" spelling -- an unrelated
// future `/collection/{id}` route on the site (a different entity type
// entirely) must not be able to trigger a bogus membership push just
// because its URL shape happens to look close enough. (`collections.js`'s
// `matchCollectionLinks` keeps a separate, narrowly-scoped singular
// tolerance for anchor-derived links, where matches are already
// pre-filtered to ids we know are real collections -- see its own doc.)
const COLLECTION_DETAIL_PATH_PATTERN = /^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?collections\/(\d+)(?:-[^/?#]*)?\/?$/i;

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
 * @param {string} url
 * @returns {"makerworld"|"thingiverse"|"printables"|null}
 */
export function detectSite(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const host = (parsed.hostname || "").toLowerCase();
  for (const entry of SITES) {
    if (entry.hosts.has(host)) {
      return entry.site;
    }
  }
  return null;
}

/**
 * True iff `url` is on a supported gallery host AND matches that site's
 * model-id pattern (path segment or query string, per site).
 * @param {string} url
 * @returns {boolean}
 */
export function isModelPage(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  const host = (parsed.hostname || "").toLowerCase();
  const entry = SITES.find((s) => s.hosts.has(host));
  if (!entry) {
    return false;
  }
  // Test against pathname + search so query-string id shapes (Thingiverse's
  // `?thing=<id>`) match without needing the full href (which would also
  // match a fragment/hash containing digits after "thing:" incidentally —
  // acceptable for this loose gate).
  const target = `${parsed.pathname}${parsed.search}`;
  return entry.idPattern.test(target);
}

/**
 * True iff `url` is a MakerWorld user's collections page --
 * `/@<handle>/collections`, optionally locale-prefixed and/or with a
 * trailing path segment or query string. Used to gate the popup's "Sync
 * collections to app" button (`extractFavoritesListFrom` in `collections.js`
 * does the actual scrape).
 * @param {string} url
 * @returns {boolean}
 */
export function isCollectionsPage(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  const host = (parsed.hostname || "").toLowerCase();
  if (!MAKERWORLD_HOSTS.has(host)) {
    return false;
  }
  return COLLECTIONS_PATH_PATTERN.test(parsed.pathname);
}

/**
 * True iff `url` is a MakerWorld collection DETAIL page -- ground-truth
 * shape `/collections/<id>[-slug]`, optionally locale-prefixed, NO `@handle`
 * segment (`COLLECTION_DETAIL_PATH_PATTERN` above; a real capture:
 * `https://makerworld.com/en/collections/18925823-esp32`). Used to gate the
 * popup's "Sync this collection to app" button and the background auto-
 * sync's per-page trigger (`syncFlow.js`'s `syncCollectionDetail`) --
 * distinct from `isCollectionsPage` above, which gates the bulk LIST sync.
 * @param {string} url
 * @returns {boolean}
 */
export function isCollectionDetailPage(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  const host = (parsed.hostname || "").toLowerCase();
  if (!MAKERWORLD_HOSTS.has(host)) {
    return false;
  }
  return COLLECTION_DETAIL_PATH_PATTERN.test(parsed.pathname);
}
