/**
 * Pure, `chrome`-free scraping of a MakerWorld collections page's data
 * (either the inline `__NEXT_DATA__` payload or a fresh `/_next/data/...`
 * route fetch -- see `syncFlow.js`'s `readCollectionsPage`) into the shape
 * `POST /ext/collections` expects, plus the pure planning/mapping helpers
 * for pushing each collection's ITEMS (M10 Workstream A task 3 -- `POST
 * /ext/collections/{list_id}/items`). The popup reads `__NEXT_DATA__`/does
 * the actual in-page `fetch`es off the live page via
 * `chrome.scripting.executeScript` (untested, thin wiring in `popup.js`)
 * and hands the results to the pure functions here.
 */

/**
 * MakerWorld hosts and the locale path prefix -- the single source for
 * `detect.js` and this module. `LOCALE_PREFIX` is a regex-source fragment
 * matching an optional leading locale segment with its trailing slash
 * (`en/`, `pt-br/`, `zh-hant/`); deliberately loose, it only needs to
 * recognize the site's own prefixes.
 */
export const MAKERWORLD_HOSTS = new Set(["makerworld.com", "www.makerworld.com"]);
export const LOCALE_PREFIX = "(?:[a-z]{2,3}(?:-[a-z0-9]{2,4})?/)?";
const LOCALE_SEGMENT_RE = /^[a-z]{2,3}(?:-[a-z0-9]{2,4})?$/i;

/**
 * @typedef {{
 *   list_id: string,
 *   title: string,
 *   slug: string|null,
 *   count: number|null,
 *   is_default: boolean,
 * }} CollectionPushEntry
 */

/**
 * Locates the `favoritesList` array within either shape a collections page
 * read can hand us: the INLINE `__NEXT_DATA__` shape
 * (`{props: {pageProps: {favoritesList}}}`), or a Next.js data-route
 * response's `pageProps`, already unwrapped one level
 * (`{favoritesList: ...}`) -- see `syncFlow.js`'s `readCollectionsPage`,
 * which tries the route first (fresh) and falls back to the inline snapshot
 * (can go stale across a client-side SPA navigation). Returns `null` when
 * neither shape yields an array, so callers can distinguish "found an
 * array (even an empty one)" from "found nothing at all".
 * @param {unknown} pagePropsOrNextData
 * @returns {Array|null}
 */
function locateFavoritesList(pagePropsOrNextData) {
  const pageProps = pagePropsOrNextData?.props?.pageProps ?? pagePropsOrNextData;
  const favoritesList = pageProps?.favoritesList;
  return Array.isArray(favoritesList) ? favoritesList : null;
}

/**
 * True iff `pagePropsOrNextData` (see `locateFavoritesList` above for the
 * two accepted shapes) carries a `favoritesList` array at all -- regardless
 * of whether it's empty. Used by `readCollectionsPage` to tell "the page
 * genuinely has zero collections" apart from "couldn't find the field at
 * all" (stale/malformed snapshot), which need different user-facing
 * messages (`syncFlow.js`).
 * @param {unknown} pagePropsOrNextData
 * @returns {boolean}
 */
export function hasFavoritesList(pagePropsOrNextData) {
  return locateFavoritesList(pagePropsOrNextData) !== null;
}

/**
 * Maps a MakerWorld collections page's `favoritesList` (same field the
 * backend's SSR-scrape importer reads -- see
 * `backend/app/importers/makerworld.py`'s `list_user_lists`) to the entries
 * `POST /ext/collections` expects. Accepts either shape `locateFavoritesList`
 * does (the inline `__NEXT_DATA__` object, or a data route's `pageProps`
 * already unwrapped) so one function serves both `readCollectionsPage`
 * read paths. Entries without an `id` or `title` are dropped. Unlike the
 * backend importer (and this function's own prior behavior), PRIVATE
 * collections (`status !== 1`) are now INCLUDED -- this is the user's own
 * library manager syncing their own MakerWorld account, and most of a
 * user's real collections are typically private, so filtering them out
 * dropped nearly everything (live bug report). Defensive throughout -- a
 * missing/malformed `__NEXT_DATA__`/`pageProps`/`favoritesList` at any depth
 * (the page markup changed, or this ran on the wrong page) yields `[]`
 * rather than throwing.
 * @param {unknown} pagePropsOrNextData parsed `__NEXT_DATA__` script tag
 *   content, or a data-route response's `pageProps`
 * @returns {CollectionPushEntry[]}
 */
export function extractFavoritesListFrom(pagePropsOrNextData) {
  const favoritesList = locateFavoritesList(pagePropsOrNextData);
  if (!favoritesList) {
    return [];
  }

  const entries = [];
  for (const item of favoritesList) {
    if (!item) {
      continue;
    }
    const id = item.id;
    const title = item.title;
    if (!id || !title) {
      continue;
    }
    entries.push({
      list_id: String(id),
      title,
      slug: item.slug ?? null,
      count: item.designCnt ?? null,
      is_default: Boolean(item.isDefault),
    });
  }
  return entries;
}

// `/@<handle>/collections`, same path shape `detect.js`'s
// `COLLECTIONS_PATH_PATTERN` already gates the sync button on -- a page that
// reached `extractHandle` is guaranteed to match this.
const HANDLE_PATH_RE = /\/@([^/]+)\/collections(?:\/.*)?$/i;

/**
 * Best-effort MakerWorld handle (the account's `name`, e.g. "Terminalfoo")
 * for the signed-in user viewing the collections page -- needed to build
 * the favorites/items fetch URL the same way the backend's `_profile`-
 * derived `@{handle}` does (`backend/app/importers/makerworld.py`).
 *
 * The RELIABLE source is the page's own URL: a page that reached this code
 * already matched `isCollectionsPage` (`detect.js`), which requires a
 * literal `/@<handle>/collections` path segment -- so the URL always
 * succeeds on the one page type this runs on, and is tried FIRST. A couple
 * of plausible `__NEXT_DATA__` shapes are tried only as a fallback when the
 * URL can't be parsed (UNVERIFIED -- no live capture of exactly where the
 * collections page's own SSR props carry this was taken for this task), so
 * an unverified guess never overrides the value the URL guarantees.
 * @param {unknown} nextDataJson parsed `__NEXT_DATA__` script tag content
 * @param {string} url the tab's URL
 * @returns {string|null}
 */
export function extractHandle(nextDataJson, url) {
  let pathname;
  try {
    pathname = new URL(url).pathname;
  } catch {
    pathname = null;
  }
  if (pathname) {
    const match = HANDLE_PATH_RE.exec(pathname);
    if (match) {
      try {
        return decodeURIComponent(match[1]);
      } catch {
        return match[1]; // malformed %-escape: use the raw segment
      }
    }
  }
  const pageProps = nextDataJson?.props?.pageProps;
  const fromNextData =
    pageProps?.userInfo?.name ?? pageProps?.profile?.name ?? pageProps?.accountInfo?.name ?? null;
  return typeof fromNextData === "string" && fromNextData ? fromNextData : null;
}

// GROUND TRUTH (a real logged-in browser, M11): a MakerWorld collection
// DETAIL page is `/{locale}/collections/{id}-{slug}`, e.g.
// `https://makerworld.com/en/collections/18925823-esp32` -- NO `@handle`
// segment (unlike the collections LIST page, `HANDLE_PATH_RE` above), PLURAL
// "collections", and the id may or may not carry a `-slug` suffix. This
// SUPERSEDES the earlier `{collectionsPathname}/{listId}` guess
// (`syncFlow.js`'s old `collectionsPathnameFrom`, removed) that glued the id
// onto the LIST page's own `@handle`-having pathname -- a real sync
// confirmed that route finds nothing. Kept tolerant of a locale prefix;
// explicitly does NOT match the bare `/@handle/collections` index page (no
// `@handle` segment is accepted at all here). PLURAL-ONLY (M1 hardening):
// an unknown future `/collection/{id}` (singular) entity type must never be
// treated as one of OUR collections by this URL-parsing path -- see
// `ANCHOR_COLLECTION_DETAIL_PATH_RE` below for the one place singular IS
// still tolerated. Two capture groups: the numeric id, and the slug
// (without its leading `-`, or `undefined` when absent).
const COLLECTION_DETAIL_PATH_RE =
  new RegExp(`^\\/${LOCALE_PREFIX}collections/(\\d+)(?:-([^/?#]*))?/?$`, "i");

// Same shape as `COLLECTION_DETAIL_PATH_RE` above, but tolerant of the
// singular "collection" spelling too. Only safe HERE, in `matchCollectionLinks`:
// its matches are already pre-filtered against `listIds`, a set of ids the
// caller already knows are real collections just pushed via `pushCollections`
// -- so a singular-spelled link can only ever match an id we already trust.
// `parseCollectionDetailUrl` (and `detect.js`'s `isCollectionDetailPage` gate)
// have no such pre-filter and must stay plural-only, or an unrelated future
// `/collection/{id}` route on the site could get scraped as if it were one
// of our collections and silently push a bogus membership set (M1).
const ANCHOR_COLLECTION_DETAIL_PATH_RE =
  new RegExp(`^\\/${LOCALE_PREFIX}collections?/(\\d+)(?:-([^/?#]*))?/?$`, "i");


/**
 * Given raw anchor hrefs (absolute or relative, as collected from a
 * MakerWorld page's `a[href]` elements) and the list ids just pushed via
 * `pushCollections`, returns a `Map(listId -> pathname)` of the FIRST
 * matching REAL link found for each id -- the actual pathname the browser
 * would navigate to for that collection's detail page (ground-truth shape,
 * `ANCHOR_COLLECTION_DETAIL_PATH_RE` above, singular-tolerant -- see its own
 * doc for why that's safe only here), as opposed to a constructed guess.
 * `syncFlow.js`'s `syncCollections` tries this source FIRST, before the
 * constructed `collectionDetailPathnameFrom` fallback below, since a real
 * link can never be wrong about its own shape.
 *
 * Same-origin only (an absolute href resolving to a non-MakerWorld host is
 * skipped -- a foreign share link could coincidentally match the path
 * shape). A relative href (doesn't parse as an absolute URL) is treated as
 * same-origin by construction. Query strings/hashes are stripped. When
 * multiple hrefs match the same list id, the FIRST one wins.
 * @param {Array<string>} hrefs
 * @param {Array<string>} listIds
 * @returns {Map<string, string>}
 */
export function matchCollectionLinks(hrefs, listIds) {
  const ids = new Set((listIds || []).map(String));
  const result = new Map();
  for (const href of hrefs || []) {
    if (typeof href !== "string" || !href) {
      continue;
    }
    let pathname;
    try {
      const parsed = new URL(href);
      if (!MAKERWORLD_HOSTS.has(parsed.hostname.toLowerCase())) {
        continue; // foreign origin
      }
      pathname = parsed.pathname;
    } catch {
      // Not parseable as an absolute URL -- treat as an already-relative
      // pathname, stripping any query string/hash by hand.
      pathname = href.split("?")[0].split("#")[0];
    }
    const match = ANCHOR_COLLECTION_DETAIL_PATH_RE.exec(pathname);
    if (!match) {
      continue;
    }
    const id = match[1];
    if (!ids.has(id) || result.has(id)) {
      continue;
    }
    result.set(id, pathname.replace(/\/$/, ""));
  }
  return result;
}

/**
 * Parses a MakerWorld URL as a collection DETAIL page (ground-truth shape,
 * `COLLECTION_DETAIL_PATH_RE` above) -- used both by `detect.js`'s
 * `isCollectionDetailPage` gate and by `syncFlow.js`'s `syncCollectionDetail`
 * to pull the collection id (and slug, when present) straight out of the
 * URL the user is actually looking at.
 * @param {string} url
 * @returns {{id: string, slug: string|null}|null} `null` when `url` doesn't
 *   parse or doesn't match the collection-detail shape.
 */
export function parseCollectionDetailUrl(url) {
  let pathname;
  try {
    pathname = new URL(url).pathname;
  } catch {
    return null;
  }
  const match = COLLECTION_DETAIL_PATH_RE.exec(pathname);
  if (!match) {
    return null;
  }
  return { id: match[1], slug: match[2] || null };
}

// A plausible locale segment (`en`, `de`, `en-us`, ...) -- loose on purpose
// (this only needs to recognize the SITE'S OWN locale prefixes, not validate
// real ISO codes); a false positive here just means a made-up two-letter
// first path segment gets treated as a locale, which is harmless since the
// resulting pathname would 404 the same way an omitted locale might.

/**
 * Best-effort locale prefix (`"en"`, `"de-de"`, ...) read off `url`'s own
 * leading path segment, or `null` when there isn't one. Used by
 * `collectionDetailPathnameFrom` below to build a same-locale detail-page
 * pathname -- the collection's own data never carries a locale, but the
 * page the user is currently on does.
 * @param {string} url
 * @returns {string|null}
 */
function localeFromUrl(url) {
  let pathname;
  try {
    pathname = new URL(url).pathname;
  } catch {
    return null;
  }
  const first = pathname.split("/").filter(Boolean)[0];
  return first && LOCALE_SEGMENT_RE.test(first) ? first.toLowerCase() : null;
}

/**
 * Builds a collection's detail-page pathname straight from its OWN pushed
 * data (ground-truth shape: `/{locale}/collections/{listId}-{slug}`, e.g.
 * `/en/collections/18925823-esp32`) instead of guessing at a route derived
 * from the LIST page's pathname (the old, now-confirmed-wrong
 * `{collectionsPathname}/{listId}` construction this replaces). Omits the
 * `-slug` suffix when the entry has no slug (`/collections/{listId}`) --
 * still a plausible real route, just untested since every live capture so
 * far has carried a slug. `syncFlow.js`'s `syncCollections` tries this AFTER
 * an anchor-derived pathname (`matchCollectionLinks` above, when a real link
 * was found) since a real link is always more trustworthy than a
 * construction, however ground-truth-informed. The slug is
 * `encodeURIComponent`-ed (M2) -- it comes straight off MakerWorld's own
 * page data (`extractFavoritesListFrom`'s `item.slug`), not something this
 * extension controls, so a slug carrying a space/`%`/`#`/`?` must not
 * corrupt the constructed pathname (a literal `#`/`?` would otherwise be
 * read back as the fragment/query start instead of part of the path).
 * @param {string} url the tab's current URL (only its locale prefix, if any,
 *   is used)
 * @param {string} listId
 * @param {string|null} [slug]
 * @returns {string}
 */
export function collectionDetailPathnameFrom(url, listId, slug) {
  // No locale on the current page: default to `en` (MakerWorld's data
  // routes need one; `readCollectionItemsFromDataRoute` callers also try
  // the locale-less variant).
  const locale = localeFromUrl(url) || "en";
  const prefix = `/${locale}`;
  return slug
    ? `${prefix}/collections/${listId}-${encodeURIComponent(slug)}`
    : `${prefix}/collections/${listId}`;
}

// Checked (in this order) before falling back to a shallow deep-scan --
// plausible field names for a collection DETAIL page's own metadata object
// (distinct from `DESIGN_LIST_KEYS` below, which is the collection's ITEMS).
// UNVERIFIED (no live capture of exactly which key a detail page's own
// title/info carries was taken for this task) -- `findCollectionTitleIn`
// below is deliberately tolerant for the same reason `findDesignListIn` is.
const COLLECTION_INFO_KEYS = ["favoritesInfo", "collectionInfo", "listInfo", "info", "detail"];

/**
 * True iff `value` looks like a collection-METADATA object (as opposed to a
 * design/model object, `looksLikeDesign` below) describing the collection
 * identified by `id`: a plain object whose own `id` stringifies to `id` and
 * that carries a non-empty string `title`.
 * @param {unknown} value
 * @param {string} id
 * @returns {boolean}
 */
function looksLikeCollectionInfo(value, id) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  if (typeof value.title !== "string" || !value.title) {
    return false;
  }
  return String(value.id) === id;
}

/**
 * A `looksLikeCollectionInfo`-matched object's own item count, when present
 * (I1 hardening): `designCnt` (the same field a `favoritesList` entry
 * carries, `extractFavoritesListFrom`) first, then the more generic `count`,
 * whichever is actually a NUMBER. `null` means "not derivable from this
 * object" -- distinct from a genuinely-empty collection's explicit `0`.
 * @param {Record<string, unknown>} value
 * @returns {number|null}
 */
function extractCollectionCount(value) {
  if (typeof value.designCnt === "number") {
    return value.designCnt;
  }
  if (typeof value.count === "number") {
    return value.count;
  }
  return null;
}

/**
 * Tolerantly locates the CURRENT collection's own title AND item count
 * within a collection detail page's `pageProps` (`readCollectionDetailPage`,
 * `syncFlow.js`) -- the detail-page counterpart to `findDesignListIn`'s
 * tolerant item-array discovery, same UNVERIFIED-field-name caveat
 * (`COLLECTION_INFO_KEYS` above). Tries the known plausible keys first, then
 * falls back to a shallow deep-scan (top level, and one level into any
 * nested plain object) for ANY object matching `looksLikeCollectionInfo`.
 * `favoritesList` and the design-list keys are excluded from the deep-scan
 * (never a single collection's own metadata). Returns `null` when nothing
 * matches -- callers (`syncFlow.js`'s `syncCollectionDetail`) treat that as
 * "title not derivable" and push items only, per its doc.
 *
 * The `count` half (I1 hardening) is what lets `syncCollectionDetail` tell a
 * COMPLETE single-SSR-response item read apart from a TRUNCATED one: that
 * flow has no paged fallback to retry a short read with (unlike
 * `syncCollections`'s bulk discovery, whose `/api/v1` fallback is
 * uid-aggregate-only anyway, M11) -- a derivable count that exceeds the read
 * item count is the only signal available that the page silently cut the
 * list short. `count` is `null` on the returned object whenever
 * `extractCollectionCount` couldn't find a numeric field on the matched
 * object -- "count not derivable," handled by the caller as its own
 * (conservative) case, never conflated with "the collection has zero items."
 * @param {unknown} pageProps
 * @param {string} id the collection id from the URL (`parseCollectionDetailUrl`)
 * @returns {{title: string, count: number|null}|null}
 */
export function findCollectionTitleIn(pageProps, id) {
  if (!pageProps || typeof pageProps !== "object" || !id) {
    return null;
  }

  for (const key of COLLECTION_INFO_KEYS) {
    const candidate = pageProps[key];
    if (looksLikeCollectionInfo(candidate, id)) {
      return { title: candidate.title, count: extractCollectionCount(candidate) };
    }
  }

  for (const [key, value] of Object.entries(pageProps)) {
    if (COLLECTION_INFO_KEYS.includes(key) || key === "favoritesList") {
      continue;
    }
    if (looksLikeCollectionInfo(value, id)) {
      return { title: value.title, count: extractCollectionCount(value) };
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [nestedKey, nestedValue] of Object.entries(value)) {
        if (nestedKey === "favoritesList") {
          continue;
        }
        if (looksLikeCollectionInfo(nestedValue, id)) {
          return { title: nestedValue.title, count: extractCollectionCount(nestedValue) };
        }
      }
    }
  }

  return null;
}

/**
 * @typedef {{
 *   external_id: string,
 *   title: string,
 *   url: string,
 *   author: string|null,
 *   thumbnail_url: string|null,
 * }} CollectionItemPushEntry
 */

/**
 * Maps one page of MakerWorld's `/design-service/favorites/designs/{listId}`
 * response (`{hits: [design...], total}`) to the shape `POST /ext/
 * collections/{list_id}/items` expects. Mirrors the backend's own mapping
 * for the SAME endpoint (`MakerWorldImporter.list_list_items`'s
 * `SearchResult` construction, `backend/app/importers/makerworld.py`) --
 * same fields, same URL shape -- but, like `extractFavoritesListFrom` above,
 * drops any entry missing an id or a title rather than the backend's
 * `f"model {id}"` placeholder-title fallback (this is a client-side scrape
 * of live page data, not an authoritative import -- silently dropping a
 * malformed hit is safer than pushing a placeholder title into the app).
 * Defensive throughout -- a missing/malformed `hits` array yields `[]`.
 * @param {unknown} designHitsResponse parsed favorites-endpoint response JSON
 * @returns {CollectionItemPushEntry[]}
 */
export function mapDesignHits(designHitsResponse) {
  const hits = designHitsResponse?.hits;
  if (!Array.isArray(hits)) {
    return [];
  }

  const items = [];
  for (const design of hits) {
    if (!design) {
      continue;
    }
    const id = design.id;
    const title = design.title;
    if (!id || !title) {
      continue;
    }
    items.push({
      external_id: String(id),
      title,
      url: `https://makerworld.com/models/${id}`,
      author: design.designCreator?.name ?? null,
      thumbnail_url: design.cover ?? null,
    });
  }
  return items;
}

// Checked (in this order) before falling back to the generic deep-scan --
// plausible field names for a per-collection design list on the
// collection's own SSR data route. UNVERIFIED (no live capture of exactly
// which key the route uses was taken for this task -- see
// `syncFlow.js`'s `readCollectionItemsFromDataRoute` doc for why this needs
// to be tolerant at all: the old `/api/v1/.../favorites/designs/{listId}`
// in-page fetch produced zero items on a real sync). `"designs"` and
// `"favoritesDesigns"` are SPECIFIC to a single collection's own item list;
// `"list"` is generic enough that it carries no such guarantee on its own
// (F1 hardening -- see `TRUSTED_EMPTY_KEYS` below).
const DESIGN_LIST_KEYS = ["designs", "favoritesDesigns", "list"];

// The subset of `DESIGN_LIST_KEYS` whose EMPTY array is still trusted as
// "this collection genuinely has zero items" rather than "wrong array" (F1
// hardening). The generic `"list"` key is deliberately excluded: MakerWorld's
// own collections-LIST payload (`favoritesList`, `extractFavoritesListFrom`)
// is exactly the kind of array that could plausibly land under a
// similarly-generic key too, so an empty `"list"` proves nothing and must
// not short-circuit the search -- see `findDesignListIn` below.
const TRUSTED_EMPTY_KEYS = new Set(["designs", "favoritesDesigns"]);

// Never a collection's OWN items -- this is the collections-LIST field
// itself (`extractFavoritesListFrom`'s source, mapped straight off
// `favoritesList` entries). Excluded from BOTH the named-key check and the
// deep-scan fallback in `findDesignListIn` below (F1 hardening, the
// important fix): a collection data-route response that also happens to
// carry the account's full collections list under `pageProps.favoritesList`
// must never be mistaken for THIS collection's items, even though a
// `favoritesList` entry carries an `id` and a `title` just like a design
// object would.
const EXCLUDED_KEYS = new Set(["favoritesList"]);

/**
 * True iff `value` looks like one MakerWorld design/model object: an object
 * with a numeric `id` and a `title`- or `name`-ish string field, and NEITHER
 * of the markers that identify a *collection* object instead: `designCnt`
 * (a collection's item count) or `isDefault` (a collection's "is this the
 * account's default collection" flag) -- see `extractFavoritesListFrom`'s
 * `CollectionPushEntry` mapping, which reads exactly these two fields off a
 * `favoritesList` entry. This collection-marker rejection is F1 hardening's
 * shape-level backstop: `EXCLUDED_KEYS` above only blocks the field
 * literally named `favoritesList`, but a collections-list array could in
 * principle turn up under some OTHER key too (a differently-shaped route
 * response, a renamed field) -- rejecting anything carrying a collection
 * marker, regardless of which key it's under, is the required part of the
 * fix. Used both to validate a named-key candidate and to drive the
 * deep-scan fallback in `findDesignListIn` below.
 * @param {unknown} value
 * @returns {boolean}
 */
function looksLikeDesign(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    !(typeof value.id === "number" || (typeof value.id === "string" && /^\d+$/.test(value.id))) ||
    !(typeof value.title === "string" || typeof value.name === "string")
  ) {
    return false;
  }
  return !("designCnt" in value || "isDefault" in value);
}

/**
 * The design-shaped entries (`looksLikeDesign`) of `array`, or `null` when
 * it isn't a design list: strictly MORE than half of its non-null entries
 * must be design-shaped (and at least one), and the malformed minority is
 * filtered out rather than failing the whole array. An array of nothing but
 * `null`s carries no evidence (F4). An empty array can't be shape-validated
 * -- callers decide separately whether it's trustworthy (see
 * `findDesignListIn`'s named-key branch, `TRUSTED_EMPTY_KEYS`).
 * @param {Array} array
 * @returns {Array|null}
 */
function filterDesigns(array) {
  const present = array.filter((item) => item !== null && item !== undefined);
  const designs = present.filter(looksLikeDesign);
  // Majority rule: tolerate a few malformed entries, but an array that is
  // mostly something else is not a design list.
  return designs.length > 0 && designs.length * 2 > present.length ? designs : null;
}

/**
 * Tolerantly locates the array of design-shaped objects within a
 * collection's own SSR data-route `pageProps` (M10 Workstream: the field
 * name carrying a *named* collection's items on this route was NOT
 * captured live for this task -- the old `/api/v1/design-service/favorites
 * /designs/{listId}` in-page fetch this is now the PRIMARY replacement for
 * produced zero items on a real sync, either because handle extraction
 * failed or because that endpoint is uid-aggregate-only even in a real
 * browser). Tries known plausible keys first (`DESIGN_LIST_KEYS`, in
 * order) -- a non-empty match there is shape-validated, and an EMPTY array
 * under a SPECIFIC named key (`TRUSTED_EMPTY_KEYS`) is still trusted (the
 * key name itself is the signal: "this collection genuinely has zero items"
 * is a legitimate outcome, and an empty array can't be shape-validated
 * anyway) -- but an empty array under the GENERIC `"list"` key carries no
 * such guarantee and does NOT short-circuit the search (F1 hardening).
 * Only when no named key matches does it fall back to a generic deep-scan
 * (top level, and one level into any nested plain object, both skipping
 * `EXCLUDED_KEYS`) for ANY non-empty array whose items are design-shaped --
 * deep-scan needs shape validation AND non-emptiness to have any confidence
 * it found the right thing, since the key name carries no signal there.
 * Returns `null` when nothing matches at either level, so callers know to
 * fall back to the `/api/v1` endpoint.
 * @param {unknown} pageProps a collection data-route response's `pageProps`
 * `designs` holds only the design-shaped entries; `rawLength` is the length
 * of the array as the page served it (what a partial-read check must compare
 * against the collection's count, since filtered/unmappable entries still
 * count toward what the server returned).
 * @returns {{key: string, designs: Array, rawLength: number}|null}
 */
export function findDesignListIn(pageProps) {
  if (!pageProps || typeof pageProps !== "object") {
    return null;
  }

  for (const key of DESIGN_LIST_KEYS) {
    const candidate = pageProps[key];
    if (!Array.isArray(candidate)) {
      continue;
    }
    if (candidate.length === 0) {
      if (TRUSTED_EMPTY_KEYS.has(key)) {
        return { key, designs: candidate, rawLength: 0 };
      }
      continue; // generic "list": an empty array proves nothing -- keep looking
    }
    const designs = filterDesigns(candidate);
    if (designs) {
      return { key, designs, rawLength: candidate.length };
    }
  }

  for (const [key, value] of Object.entries(pageProps)) {
    if (EXCLUDED_KEYS.has(key)) {
      continue; // the collections LIST itself, never a collection's items
    }
    if (DESIGN_LIST_KEYS.includes(key)) {
      continue; // already checked above
    }
    const topDesigns = Array.isArray(value) ? filterDesigns(value) : null;
    if (topDesigns) {
      return { key, designs: topDesigns, rawLength: value.length };
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [nestedKey, nestedValue] of Object.entries(value)) {
        if (EXCLUDED_KEYS.has(nestedKey)) {
          continue;
        }
        const nestedDesigns = Array.isArray(nestedValue) ? filterDesigns(nestedValue) : null;
        if (nestedDesigns) {
          return { key: `${key}.${nestedKey}`, designs: nestedDesigns, rawLength: nestedValue.length };
        }
      }
    }
  }

  return null;
}
