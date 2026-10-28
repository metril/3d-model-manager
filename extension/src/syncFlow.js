/**
 * Shared MakerWorld collections-sync orchestration. BOTH the popup (manual
 * "Sync collections to app" button, `popup.js`) and the background service
 * worker (auto-sync on page visit, `background.js`) drive this exact flow.
 *
 * `chrome.*` is never called here directly -- the impure seams are all
 * injected, so this module stays unit-testable with stubs:
 *   - `exec(tabId, func, args)`: wraps `chrome.scripting.executeScript`
 *     (`exec.js`), resolving to the injected function's return value. Used to
 *     read the page's `__NEXT_DATA__`/data route and to fetch each
 *     collection's items out of the page (the page's own origin carries the
 *     browser's cookies/`cf_clearance` a background-worker `fetch` couldn't).
 *   - `api`: an `api.js` client (`pushCollections`/`upsertCollection`/
 *     `pushCollectionItems`).
 *   - `report(text, kind)`: status callback (`kind`: null | "ok" | "warn" |
 *     "error"). The popup wires it to its status line; the background worker
 *     wires it to `console.*` only.
 *
 * Item source: each collection's own SSR data route
 * (`/_next/data/<buildId>/<locale>/collections/<id>-<slug>.json`), read
 * tolerantly by `findDesignListIn`. There is NO uid-aggregate `/api/v1`
 * fallback: that endpoint ignores the list id and can return ALL favorites,
 * which would overwrite a collection's membership with the wrong set.
 *
 * `syncCollections` resolves to `{collections, items, unreadable, failed,
 * partial}` and REJECTS on a hard failure (page unreadable, no collections,
 * list push rejected). `syncCollectionDetail` is the guaranteed-correct
 * single-collection flow for the page the user is looking at.
 */

import {
  collectionDetailPathnameFrom,
  extractFavoritesListFrom,
  findCollectionTitleIn,
  findDesignListIn,
  hasFavoritesList,
  mapDesignHits,
  matchCollectionLinks,
  parseCollectionDetailUrl,
} from "./collections.js";
import { hashToken } from "./courier.js";

/** Per-collection reads/pushes run with this many in flight. */
const CONCURRENCY = 4;

// Keys whose array is specific to ONE collection's items (an empty one is a
// trustworthy "zero items"); mirrors `collections.js`'s `TRUSTED_EMPTY_KEYS`.
const NAMED_LIST_KEYS = new Set(["designs", "favoritesDesigns"]);

/**
 * Runs `fn(item, index)` over `items` with at most `limit` in flight and
 * resolves to the results in input order. `fn` is expected not to throw
 * (callers wrap their own try/catch); a throw rejects the whole call.
 * @template T, R
 * @param {T[]} items
 * @param {number} limit
 * @param {(item: T, index: number) => Promise<R>} fn
 * @returns {Promise<R[]>}
 */
export async function mapLimit(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

/**
 * Executed IN THE PAGE (via the injected `exec`), not the worker or popup --
 * `chrome.scripting` serializes this function's source and re-evaluates it
 * in the page, so it cannot close over anything from this module.
 *
 * Reads the inline `__NEXT_DATA__` AND -- when it carries a `buildId` -- the
 * page's own Next.js data route (`/_next/data/<buildId><pathname>.json`) for
 * a FRESH copy of the page's props (the inline snapshot goes stale across a
 * client-side SPA navigation). To keep the injection result small, only
 * array fields and one level of shallow objects are returned (not the whole
 * `pageProps`).
 *
 * `expectedPathname` guards the navigation race: if the tab is no longer on
 * that path (before or after the fetch) the read is abandoned with
 * `navigated: true` instead of returning another page's data.
 * @param {string|null} expectedPathname
 * @returns {Promise<{navigated?: boolean, nextData: unknown, routePageProps: unknown}>}
 */
async function readCollectionsDataInPage(expectedPathname) {
  const norm = (p) => String(p || "").replace(/\/$/, "");
  const moved = () => Boolean(expectedPathname) && norm(location.pathname) !== norm(expectedPathname);
  if (moved()) {
    return { navigated: true, nextData: null, routePageProps: null };
  }

  const slimPageProps = (pageProps) => {
    if (!pageProps || typeof pageProps !== "object") {
      return null;
    }
    const out = {};
    for (const [key, value] of Object.entries(pageProps)) {
      if (Array.isArray(value)) {
        out[key] = value;
      } else if (value && typeof value === "object") {
        const inner = {};
        for (const [innerKey, innerValue] of Object.entries(value)) {
          if (Array.isArray(innerValue)) {
            inner[innerKey] = innerValue;
          } else if (innerValue === null || typeof innerValue !== "object") {
            inner[innerKey] =
              typeof innerValue === "string" && innerValue.length > 300 ? innerValue.slice(0, 300) : innerValue;
          }
        }
        out[key] = inner;
      }
    }
    return out;
  };

  let rawNextData;
  try {
    rawNextData = JSON.parse(document.getElementById("__NEXT_DATA__")?.textContent ?? "null");
  } catch {
    rawNextData = null;
  }
  const nextData = rawNextData
    ? { buildId: rawNextData.buildId, props: { pageProps: slimPageProps(rawNextData.props?.pageProps) } }
    : null;

  let routePageProps = null;
  const buildId = rawNextData?.buildId;
  if (buildId) {
    try {
      const response = await fetch(`/_next/data/${buildId}${norm(location.pathname)}.json`, {
        credentials: "include",
        signal: AbortSignal.timeout(15000),
      });
      if (response.ok) {
        const routeJson = await response.json();
        routePageProps = slimPageProps(routeJson?.pageProps);
      }
    } catch {
      routePageProps = null;
    }
  }

  if (moved()) {
    return { navigated: true, nextData: null, routePageProps: null };
  }
  return { nextData, routePageProps };
}

/**
 * Executed IN THE PAGE (same self-contained constraint). Collects every
 * same-origin `a[href]` pathname that loosely looks like a collection link;
 * the PRECISE matching happens in `matchCollectionLinks` (pure, tested).
 * @param {string|null} expectedPathname navigation-race guard, as above
 * @returns {string[]}
 */
function collectCollectionAnchorPathnamesInPage(expectedPathname) {
  const norm = (p) => String(p || "").replace(/\/$/, "");
  if (expectedPathname && norm(location.pathname) !== norm(expectedPathname)) {
    return [];
  }
  const seen = new Set();
  const pathnames = [];
  for (const anchor of document.querySelectorAll("a[href]")) {
    const href = anchor.href;
    if (!href) {
      continue;
    }
    let url;
    try {
      url = new URL(href);
    } catch {
      continue;
    }
    if (url.origin !== location.origin || !/\/collections?\//i.test(url.pathname)) {
      continue;
    }
    if (!seen.has(url.pathname)) {
      seen.add(url.pathname);
      pathnames.push(url.pathname);
    }
  }
  return pathnames;
}

/**
 * Executed IN THE PAGE. Fetches ONE collection's own SSR data route for an
 * already-known pathname. Aborts (null) when the tab navigated away
 * (`expectedPathname`) and times out after 15s.
 * @param {string} buildId
 * @param {string} collectionPathname e.g. `/en/collections/18925823-esp32`
 * @param {string|null} expectedPathname navigation-race guard
 * @returns {Promise<unknown>} parsed data-route JSON, or `null` on any failure
 */
async function fetchCollectionDataRouteInPage(buildId, collectionPathname, expectedPathname) {
  const norm = (p) => String(p || "").replace(/\/$/, "");
  if (expectedPathname && norm(location.pathname) !== norm(expectedPathname)) {
    return null;
  }
  try {
    const response = await fetch(`/_next/data/${buildId}${collectionPathname}.json`, {
      credentials: "include",
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      return null;
    }
    const routeJson = await response.json();
    if (expectedPathname && norm(location.pathname) !== norm(expectedPathname)) {
      return null;
    }
    // Only the candidate arrays (and shallow info objects) cross back, not
    // the whole pageProps.
    const pageProps = routeJson?.pageProps;
    if (!pageProps || typeof pageProps !== "object") {
      return { pageProps: null };
    }
    const slim = {};
    for (const [key, value] of Object.entries(pageProps)) {
      if (Array.isArray(value)) {
        slim[key] = value;
      } else if (value && typeof value === "object") {
        const inner = {};
        for (const [innerKey, innerValue] of Object.entries(value)) {
          if (Array.isArray(innerValue)) {
            inner[innerKey] = innerValue;
          }
        }
        slim[key] = inner;
      }
    }
    return { pageProps: slim };
  } catch {
    return null;
  }
}

/**
 * Reads one collection's items off a candidate SSR data-route pathname.
 * Logs which key matched via `report` (informational). Never throws.
 *
 * Returns `found` (a trusted design list was located -- possibly empty),
 * `rawLength` (the array length as served, for the partial-read check) and a
 * `diagnostic` string with top-level `pageProps` KEY NAMES only (never values).
 * @returns {Promise<{items: import("./collections.js").CollectionItemPushEntry[], found: boolean, rawLength: number, diagnostic: string}>}
 */
async function readCollectionItemsFromDataRoute(
  tabId,
  buildId,
  collectionPathname,
  entryTitle,
  exec,
  report,
  expectedPathname,
) {
  let routeJson;
  try {
    routeJson = await exec(tabId, fetchCollectionDataRouteInPage, [buildId, collectionPathname, expectedPathname]);
  } catch {
    return { items: [], found: false, rawLength: 0, diagnostic: "route unreachable" };
  }
  if (!routeJson) {
    return { items: [], found: false, rawLength: 0, diagnostic: "route 404 or unavailable" };
  }
  const found = findDesignListIn(routeJson?.pageProps);
  if (!found) {
    const pageProps = routeJson?.pageProps;
    const keys =
      pageProps && typeof pageProps === "object" ? Object.keys(pageProps).join(", ") || "(none)" : "(not an object)";
    return {
      items: [],
      found: false,
      rawLength: 0,
      diagnostic: `route ok but no design array (pageProps keys: ${keys})`,
    };
  }
  report(`Matched items for "${entryTitle}" via pageProps.${found.key}.`, null);
  return {
    items: mapDesignHits({ hits: normalizeDesigns(found.designs) }),
    found: true,
    key: found.key,
    rawLength: found.rawLength,
    diagnostic: `route ok, matched pageProps.${found.key}`,
  };
}

/** `findDesignListIn` accepts a `name` field standing in for `title`; `mapDesignHits` only knows `title`. */
function normalizeDesigns(designs) {
  return designs.map((design) => (design && !design.title && design.name ? { ...design, title: design.name } : design));
}

/**
 * Composes the ONE diagnostic line surfaced for the FIRST collection that
 * stays unreadable. Never includes field VALUES (key names, pathnames and
 * counts only).
 * @param {string} title
 * @param {{anchorPathname: string|null, attempts: Array<{label: string, pathname: string, diagnostic: string}>}} info
 * @returns {string}
 */
export function describeUnreadableCollection(title, { anchorPathname, attempts }) {
  const attemptText = (attempts || [])
    .map((attempt) => `${attempt.label} (${attempt.pathname}): ${attempt.diagnostic}`)
    .join("; ");
  const anchorText = anchorPathname ? `found (${anchorPathname})` : "not found";
  return `Diagnostic for "${title}" -- anchor link ${anchorText}` + (attemptText ? `; ${attemptText}` : "") + ".";
}

/**
 * `url`'s pathname (for the navigation-race guard), or `null` when `url` is
 * absent/unparseable (guard disabled).
 */
function pathnameOf(url) {
  try {
    return url ? new URL(url).pathname : null;
  } catch {
    return null;
  }
}

/**
 * Reads the collections page's live data and extracts its favorites list,
 * preferring the FRESH data route over the inline snapshot (stale after SPA
 * navigation). The route is trusted whenever it yields a `favoritesList`
 * array AT ALL, even an empty one; the inline snapshot is the fallback.
 *
 * @param {{tabId: number, url?: string, exec: (tabId: number, func: Function, args?: unknown[]) => Promise<unknown>}} opts
 *   `url` (the tab's URL) enables the navigation-race guard.
 * @returns {Promise<{nextData: unknown, entries: import("./collections.js").CollectionPushEntry[], found: boolean}|null>}
 *   `null` when the page couldn't be read (injection threw, or the tab
 *   navigated away mid-read). `found` is `true` when a `favoritesList` array
 *   was located (even if `entries` is empty).
 */
export async function readCollectionsPage({ tabId, url, exec }) {
  let page;
  try {
    page = await exec(tabId, readCollectionsDataInPage, [pathnameOf(url)]);
  } catch {
    return null;
  }
  if (page?.navigated) {
    return null;
  }
  const { nextData, routePageProps } = page ?? {};
  if (hasFavoritesList(routePageProps)) {
    return { nextData, entries: extractFavoritesListFrom(routePageProps), found: true };
  }
  if (hasFavoritesList(nextData)) {
    return { nextData, entries: extractFavoritesListFrom(nextData), found: true };
  }
  return { nextData, entries: [], found: false };
}

/**
 * SHA-256 hash (hex) of a `readCollectionsPage` result's `entries` -- the
 * payload the auto-sync throttle compares against `lastCollectionsHash`.
 * @param {Array} entries
 * @returns {Promise<string>}
 */
export function hashCollectionsPayload(entries) {
  return hashToken(JSON.stringify(entries ?? []));
}

/**
 * True when `entries`' hash differs from `lastHash`.
 * @param {Array} entries
 * @param {string|null|undefined} lastHash
 * @returns {Promise<boolean>}
 */
export async function shouldPushCollections(entries, lastHash) {
  const hash = await hashCollectionsPayload(entries);
  return hash !== lastHash;
}

/**
 * True when a `syncCollections` result should have its throttle hash
 * persisted -- only on a fully-clean run: nothing `unreadable`, nothing
 * `failed` (push rejected) and nothing `partial`. Any of those must leave the
 * hash alone so the next visit retries.
 * @param {{unreadable: string[], failed?: string[], partial?: string[]}} result
 * @returns {boolean}
 */
export function shouldPersistHash(result) {
  return (
    result.unreadable.length === 0 && (result.failed?.length ?? 0) === 0 && (result.partial?.length ?? 0) === 0
  );
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * Candidate data-route pathnames for one collection, in preference order:
 * the anchor-derived real link, the constructed `/{locale}/collections/...`
 * (locale defaults to `en`), and the locale-less variant. Deduplicated.
 */
function candidatePathnames(anchorPathname, url, entry) {
  const constructed = collectionDetailPathnameFrom(url, entry.list_id, entry.slug);
  const localeless = constructed.replace(/^\/[^/]+(?=\/collections\/)/, "");
  const out = [];
  const add = (label, pathname) => {
    if (pathname && !out.some((c) => c.pathname === pathname)) {
      out.push({ label, pathname });
    }
  };
  add("anchor", anchorPathname);
  add("constructed", constructed);
  add("locale-less", localeless);
  return out;
}

/**
 * Runs the full collections sync: read the page, push the collection list,
 * then read+push each collection's items (bounded concurrency, each
 * collection isolated by its own try/catch).
 *
 * Per-collection outcomes:
 *   - items read and pushed -> counted; `partial` when the RAW array served
 *     is shorter than the collection's known count;
 *   - trusted-empty list with a known count of 0 -> pushes `[]` so removals
 *     propagate;
 *   - nothing readable -> `unreadable` (nothing pushed);
 *   - the push was rejected/threw -> `failed` (distinct from unreadable).
 * @param {object} opts
 * @param {number} opts.tabId
 * @param {string} opts.url the tab's URL (locale for constructed pathnames, navigation guard)
 * @param {(tabId: number, func: Function, args?: unknown[]) => Promise<unknown>} opts.exec
 * @param {{pushCollections: Function, pushCollectionItems: Function}} opts.api
 * @param {(text: string, kind: string|null) => void} opts.report
 * @param {{nextData: unknown, entries: Array, found: boolean}} [opts.page] pre-fetched `readCollectionsPage` result
 * @returns {Promise<{collections: number, items: number, unreadable: string[], failed: string[], partial: string[]}>}
 */
export async function syncCollections({ tabId, url, exec, api, report, page }) {
  report("Reading collections…", null);

  const resolvedPage = page ?? (await readCollectionsPage({ tabId, url, exec }));
  if (!resolvedPage) {
    const message = "Couldn't read this page.";
    report(message, "error");
    throw new Error(message);
  }

  const { nextData, entries, found } = resolvedPage;
  if (entries.length === 0) {
    const message = found
      ? "No collections in your MakerWorld account yet."
      : "Couldn't read your collections from this page — try a hard refresh (Ctrl+Shift+R) and click again.";
    report(message, "error");
    throw new Error(message);
  }

  report("Syncing…", null);
  const listResult = await api.pushCollections("makerworld", entries);
  if (!listResult.ok) {
    const message = listResult.error || "Something went wrong.";
    report(message, "error");
    throw new Error(message);
  }

  const buildId = nextData?.buildId ?? null;
  const guard = pathnameOf(url);

  // Collect every anchor ONCE and match against the pushed list ids.
  let anchorPathnames = new Map();
  if (buildId) {
    let anchorHrefs = [];
    try {
      anchorHrefs = (await exec(tabId, collectCollectionAnchorPathnamesInPage, [guard])) || [];
    } catch {
      anchorHrefs = [];
    }
    anchorPathnames = matchCollectionLinks(
      anchorHrefs,
      entries.map((entry) => entry.list_id),
    );
  }

  let done = 0;
  const processEntry = async (entry) => {
    const anchorPathname = anchorPathnames.get(entry.list_id) || null;
    const attempts = [];
    let primary = { items: [], found: false, rawLength: 0 };
    if (buildId) {
      for (const candidate of candidatePathnames(anchorPathname, url, entry)) {
        const attempt = await readCollectionItemsFromDataRoute(
          tabId,
          buildId,
          candidate.pathname,
          entry.title,
          exec,
          report,
          guard,
        );
        attempts.push({ ...candidate, diagnostic: attempt.diagnostic });
        if (attempt.found) {
          primary = attempt;
          break;
        }
      }
    }

    // A trusted-but-empty list: only believe it (and push `[]` so removals
    // propagate) when the collection's own count agrees it is empty.
    if (primary.found && primary.rawLength === 0) {
      if (entry.count === 0) {
        const emptyResult = await api.pushCollectionItems("makerworld", entry.list_id, []);
        return emptyResult.ok ? { status: "ok", pushed: 0 } : { status: "failed" };
      }
      if (entry.count == null) {
        return { status: "skipped" }; // can't tell empty from a transient empty read
      }
    }

    if (primary.items.length === 0) {
      return { status: "unreadable", diagnostic: describeUnreadableCollection(entry.title, { anchorPathname, attempts }) };
    }

    const itemsResult = await api.pushCollectionItems("makerworld", entry.list_id, primary.items);
    if (!itemsResult.ok) {
      return { status: "failed" };
    }
    // Partial: the raw array served is shorter than the known count.
    const partial = entry.count != null && primary.rawLength < entry.count;
    return { status: partial ? "partial" : "ok", pushed: primary.items.length };
  };

  const outcomes = await mapLimit(entries, CONCURRENCY, async (entry) => {
    let outcome;
    try {
      outcome = await processEntry(entry);
    } catch {
      outcome = { status: "failed" };
    }
    done += 1;
    report(`Read items for "${entry.title}" (${done}/${entries.length})…`, null);
    return outcome;
  });

  let totalItems = 0;
  const unreadableTitles = [];
  const failedTitles = [];
  const partialTitles = [];
  let firstUnreadableDiagnostic = null;
  outcomes.forEach((outcome, i) => {
    const title = entries[i].title;
    totalItems += outcome.pushed ?? 0;
    if (outcome.status === "unreadable") {
      unreadableTitles.push(title);
      if (!firstUnreadableDiagnostic) {
        firstUnreadableDiagnostic = outcome.diagnostic;
        console.error(`[collections sync] ${firstUnreadableDiagnostic}`);
      }
    } else if (outcome.status === "failed") {
      failedTitles.push(title);
    } else if (outcome.status === "partial") {
      partialTitles.push(title);
    }
  });

  const suffixes = [
    unreadableTitles.length > 0 ? `; ${plural(unreadableTitles.length, "collection")} unreadable` : "",
    failedTitles.length > 0 ? `; ${plural(failedTitles.length, "collection")} failed to push` : "",
    partialTitles.length > 0 ? `; ${plural(partialTitles.length, "collection")} partial` : "",
  ].join("");
  // APPENDED to the summary: the popup only shows the LAST `report` text.
  const diagnosticSuffix = firstUnreadableDiagnostic ? ` ${firstUnreadableDiagnostic}` : "";
  const clean = shouldPersistHash({ unreadable: unreadableTitles, failed: failedTitles, partial: partialTitles });
  report(
    `Synced ${entries.length} collections (${totalItems} items${suffixes}).${diagnosticSuffix}`,
    clean ? "ok" : "warn",
  );
  return {
    collections: entries.length,
    items: totalItems,
    unreadable: unreadableTitles,
    failed: failedTitles,
    partial: partialTitles,
  };
}

/**
 * Reads a MakerWorld collection DETAIL page's live data -- the guaranteed-
 * correct item source (the user is LOOKING AT the collection's items).
 * Reuses `readCollectionsDataInPage`, then `findDesignListIn` for the items
 * and `findCollectionTitleIn` for the collection's own title and count.
 *
 * The FRESH data route is preferred. The inline `__NEXT_DATA__` (stale after
 * an SPA navigation) is only used when it carries a collection-info object
 * whose id matches this URL's id -- otherwise it is some other page's data.
 * @param {{tabId: number, url: string, exec: (tabId: number, func: Function, args?: unknown[]) => Promise<unknown>}} opts
 * @returns {Promise<{parsed: {id: string, slug: string|null}, pageProps: unknown, items: import("./collections.js").CollectionItemPushEntry[], title: string|null, count: number|null, found: boolean, key: string|null, rawLength: number}|null>}
 *   `null` when `url` isn't a collection detail page, the page couldn't be
 *   read, or the tab navigated away mid-read.
 */
export async function readCollectionDetailPage({ tabId, url, exec }) {
  const parsed = parseCollectionDetailUrl(url);
  if (!parsed) {
    return null;
  }

  let page;
  try {
    page = await exec(tabId, readCollectionsDataInPage, [pathnameOf(url)]);
  } catch {
    return null;
  }
  if (page?.navigated) {
    return null;
  }
  const { nextData, routePageProps } = page ?? {};

  let pageProps = routePageProps ?? null;
  let found = findDesignListIn(pageProps);
  if (!found) {
    const inline = nextData?.props?.pageProps ?? null;
    if (inline && findCollectionTitleIn(inline, parsed.id)) {
      pageProps = inline;
      found = findDesignListIn(inline);
    }
  }
  if (!found) {
    return { parsed, pageProps, items: [], title: null, count: null, found: false, key: null, rawLength: 0 };
  }
  const items = mapDesignHits({ hits: normalizeDesigns(found.designs) });
  const info = findCollectionTitleIn(pageProps, parsed.id);
  return {
    parsed,
    pageProps,
    items,
    title: info ? info.title : null,
    count: info ? info.count : null,
    found: true,
    key: found.key,
    rawLength: found.rawLength,
  };
}

/**
 * SHA-256 hash of a `readCollectionDetailPage` result's item ids -- the
 * payload the detail auto-sync throttle compares against
 * `lastCollectionItemsHash`. Ids only: a title/author change alone isn't a
 * membership change.
 * @param {import("./collections.js").CollectionItemPushEntry[]} items
 * @returns {Promise<string>}
 */
export function hashCollectionItemsPayload(items) {
  return hashToken(JSON.stringify((items || []).map((item) => item.external_id)));
}

// `lastCollectionItemsHash` is an ARRAY of `{listId, hash}` pairs, oldest
// first -- NOT an object keyed by listId (integer-like keys get reordered by
// every JS engine, which would break "prune to the last 50 by recency").
const DEFAULT_MAX_COLLECTION_ITEM_HASHES = 50;

/**
 * Looks up `listId`'s last-pushed items hash within `entries`.
 * @param {Array<{listId: string, hash: string}>|null|undefined} entries
 * @param {string} listId
 * @returns {string|null}
 */
export function findLastCollectionItemsHash(entries, listId) {
  const match = (entries || []).find((entry) => entry && entry.listId === listId);
  return match ? match.hash : null;
}

/**
 * Returns a NEW array with `listId`'s hash upserted at the END (most
 * recently synced), pruned to the last `limit` entries. Pure.
 * @param {Array<{listId: string, hash: string}>|null|undefined} entries
 * @param {string} listId
 * @param {string} hash
 * @param {number} [limit]
 * @returns {Array<{listId: string, hash: string}>}
 */
export function upsertCollectionItemsHash(entries, listId, hash, limit = DEFAULT_MAX_COLLECTION_ITEM_HASHES) {
  const withoutListId = (entries || []).filter((entry) => entry && entry.listId !== listId);
  withoutListId.push({ listId, hash });
  return withoutListId.slice(-limit);
}

/**
 * Syncs a SINGLE collection from its own detail page. Upserts ONE cache entry
 * (`api.upsertCollection`, never the full-replace `pushCollections`, which
 * would wipe every other cached collection) when a title is derivable, then
 * pushes the items (a replace-set on the backend).
 *
 * Completeness (items are read off ONE un-paged SSR response, so a short read
 * would silently shrink the cached membership):
 *   - count derivable and the RAW array served is shorter than it: TRUNCATED
 *     -- push NOTHING, report a warning, `partial: true`.
 *   - count NOT derivable: only believed when the list came from a named key
 *     (`designs`/`favoritesDesigns`); an unnamed/deep-scanned array is not
 *     trusted, so nothing is pushed (`partial: true`). A named-key read is
 *     pushed with `count: null` (never asserting `items.length` as the count).
 *   - count derivable and met: confirmed complete.
 * A trusted-empty read with a derived count of 0 pushes `[]` so removals
 * propagate; any other empty read pushes no items.
 *
 * `persistable` tells `background.js` whether to persist its throttle hash:
 * `!partial && (countDerived || namedKey)`.
 * @param {object} opts
 * @param {number} opts.tabId
 * @param {string} opts.url must be a collection detail page
 * @param {(tabId: number, func: Function, args?: unknown[]) => Promise<unknown>} opts.exec
 * @param {{upsertCollection: Function, pushCollectionItems: Function}} opts.api
 * @param {(text: string, kind: string|null) => void} opts.report
 * @param {Awaited<ReturnType<typeof readCollectionDetailPage>>} [opts.page] pre-fetched read
 * @returns {Promise<{listId: string, title: string|null, items: number, partial: boolean, countDerived: boolean, persistable: boolean}>}
 */
export async function syncCollectionDetail({ tabId, url, exec, api, report, page }) {
  report("Reading this collection…", null);

  const resolvedPage = page ?? (await readCollectionDetailPage({ tabId, url, exec }));
  if (!resolvedPage) {
    const message = "This isn't a collection page.";
    report(message, "error");
    throw new Error(message);
  }
  if (!resolvedPage.found) {
    const message = "Couldn't read this collection's items from the page.";
    report(message, "error");
    throw new Error(message);
  }

  const { parsed, items, title, count } = resolvedPage;
  const rawLength = resolvedPage.rawLength ?? items.length;
  const countDerived = typeof count === "number";
  const namedKey = NAMED_LIST_KEYS.has(resolvedPage.key);
  const skipped = (message) => {
    report(message, "warn");
    return { listId: parsed.id, title: title || null, items: 0, partial: true, countDerived, persistable: false };
  };

  if (countDerived && rawLength < count) {
    return skipped(`Partial read (${rawLength} of ${count}) — skipped.`);
  }
  if (!countDerived && !namedKey) {
    return skipped("Couldn't confirm this collection was read completely — skipped.");
  }

  if (title) {
    const entry = {
      list_id: parsed.id,
      title,
      slug: parsed.slug,
      count: countDerived ? count : null,
      is_default: false,
    };
    const listResult = await api.upsertCollection("makerworld", entry);
    if (!listResult.ok) {
      const message = listResult.error || "Something went wrong.";
      report(message, "error");
      throw new Error(message);
    }
  }

  // Non-empty: push. Trusted-empty with a confirmed count of 0: push `[]`.
  const trustedEmpty = rawLength === 0 && countDerived && count === 0;
  if (items.length > 0 || trustedEmpty) {
    const itemsResult = await api.pushCollectionItems("makerworld", parsed.id, items);
    if (!itemsResult.ok) {
      const message = itemsResult.error || "Something went wrong.";
      report(message, "error");
      throw new Error(message);
    }
  }

  const label = title || parsed.id;
  report(`Synced "${label}": ${plural(items.length, "item")}.`, "ok");
  return { listId: parsed.id, title: title || null, items: items.length, partial: false, countDerived, persistable: true };
}
