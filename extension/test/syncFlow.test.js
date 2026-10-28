import { test } from "node:test";
import assert from "node:assert/strict";

import {
  describeUnreadableCollection,
  findLastCollectionItemsHash,
  hashCollectionItemsPayload,
  hashCollectionsPayload,
  readCollectionDetailPage,
  readCollectionsPage,
  shouldPersistHash,
  shouldPushCollections,
  syncCollectionDetail,
  syncCollections,
  upsertCollectionItemsHash,
} from "../src/syncFlow.js";
import { hashToken } from "../src/courier.js";

const BUILD_ID = "build-abc123";
const COLLECTIONS_URL = "https://makerworld.com/en/@Terminalfoo/collections";
// Ground-truth (M11): a real collection DETAIL page is
// `/{locale}/collections/{id}-{slug}`, e.g.
// `https://makerworld.com/en/collections/18925823-esp32` -- NO `@handle`
// segment. `collectionDetailPathnameFrom` (`collections.js`) builds this
// from each entry's own `list_id`/`slug` plus the CURRENT page's locale
// (`en`, from `COLLECTIONS_URL` above), replacing the old (confirmed-wrong)
// `{collectionsPathname}/{listId}` guess this file used to assert on.
const CONSTRUCTED_PATHNAME_2155987 = "/en/collections/2155987-default-collection";
const CONSTRUCTED_PATHNAME_18925823 = "/en/collections/18925823-esp32"; // == the real ground-truth URL's own pathname

// Mirrors backend/tests/cassettes/makerworld_fixtures.py FAVORITES_LIST /
// test/collections.test.js's fixtures -- two collections. Carries a
// `buildId` (needed for the data-route reads `readCollectionsDataInPage`/
// `readCollectionItemsFromDataRoute` build) unless a test explicitly needs
// to exercise the no-buildId path. `designCnt` is deliberately lowered to 1
// per collection here (the live capture uses 7/9) -- most tests below stub a
// single item per collection to keep fixtures small, and F2 hardening now
// treats a primary/fallback read that comes up SHORTER than `designCnt` as a
// truncation signal (see `syncCollections`'s `needsFallback`/`partial`
// handling); keeping this shared fixture's `count` matched to what the
// stubs actually return avoids every unrelated test having to route through
// that machinery. The dedicated F2 tests below use their OWN fixture with a
// deliberate count/items mismatch instead.
const NEXT_DATA = {
  buildId: BUILD_ID,
  props: {
    pageProps: {
      favoritesList: [
        {
          id: 2155987,
          title: "Default Collection",
          slug: "default-collection",
          isDefault: true,
          designCnt: 1,
          status: 1,
        },
        {
          id: 18925823,
          title: "ESP32",
          slug: "esp32",
          isDefault: false,
          designCnt: 1,
          status: 1,
        },
      ],
    },
  },
};

const ENTRIES = [
  {
    list_id: "2155987",
    title: "Default Collection",
    slug: "default-collection",
    count: 1,
    is_default: true,
  },
  { list_id: "18925823", title: "ESP32", slug: "esp32", count: 1, is_default: false },
];

/** A `NEXT_DATA`-shaped fixture carrying only the first collection, for
 * tests that want a single-collection item pipeline in isolation. */
const ONE_ENTRY_NEXT_DATA = {
  buildId: BUILD_ID,
  props: { pageProps: { favoritesList: [NEXT_DATA.props.pageProps.favoritesList[0]] } },
};

/** Records calls and returns queued results/throws in call order. */
function stubExec(steps) {
  let i = 0;
  const calls = [];
  const exec = async (tabId, func, args) => {
    const step = steps[i++];
    calls.push({ tabId, func, args });
    if (!step) {
      throw new Error(`stubExec: no step queued for call #${i}`);
    }
    if (step.throws) {
      throw new Error(step.throws);
    }
    return step.result;
  };
  return { exec, calls };
}

/** A queued `stubExec` step for the page-read exec call --
 * `readCollectionsDataInPage`'s `{nextData, routePageProps}` shape. */
function pageResult({ nextData = null, routePageProps = null } = {}) {
  return { result: { nextData, routePageProps } };
}

/** A queued `stubExec` step for a per-collection PRIMARY data-route exec
 * call -- `fetchCollectionDataRouteInPage`'s raw route JSON
 * (`{pageProps, ...}`) shape. */
function designsRoute(pageProps) {
  return { result: { pageProps } };
}

/** A queued `stubExec` step for the ONE-TIME anchor-collection exec call --
 * `collectCollectionAnchorPathnamesInPage`'s raw pathname array shape. */
function anchorResult(pathnames = []) {
  return { result: pathnames };
}

function okResult(data = { ok: true }) {
  return { ok: true, status: 200, data, error: null };
}

function errResult(error, status = 422) {
  return { ok: false, status, data: null, error };
}

function stubApi({ pushCollections, pushCollectionItems } = {}) {
  const calls = { pushCollections: [], pushCollectionItems: [] };
  return {
    calls,
    api: {
      pushCollections: async (site, collections) => {
        calls.pushCollections.push({ site, collections });
        return pushCollections ?? okResult({ ok: true, count: collections.length });
      },
      pushCollectionItems: async (site, listId, items) => {
        const index = calls.pushCollectionItems.length;
        calls.pushCollectionItems.push({ site, listId, items });
        const configured = Array.isArray(pushCollectionItems)
          ? pushCollectionItems[index]
          : pushCollectionItems;
        return configured ?? okResult({ ok: true, count: items.length });
      },
    },
  };
}

function stubReport() {
  const calls = [];
  return { report: (text, kind) => calls.push({ text, kind }), calls };
}

const ITEM_A = { external_id: "1", title: "Item A" };
const ITEM_B = { external_id: "2", title: "Item B" };

test("readCollectionsPage: returns null when exec (the page read) throws", async () => {
  const { exec } = stubExec([{ throws: "no such tab" }]);
  const result = await readCollectionsPage({ tabId: 1, exec });
  assert.equal(result, null);
});

test("readCollectionsPage: route pageProps is preferred over a stale inline snapshot", async () => {
  // "Stale" the way the live bug report describes it: __NEXT_DATA__ is from
  // whatever page loaded first, so its favoritesList doesn't match what's
  // actually on the collections page right now.
  const STALE_NEXT_DATA = { buildId: BUILD_ID, props: { pageProps: { favoritesList: [] } } };
  const FRESH_ROUTE_PAGE_PROPS = { favoritesList: NEXT_DATA.props.pageProps.favoritesList };
  const { exec } = stubExec([
    pageResult({ nextData: STALE_NEXT_DATA, routePageProps: FRESH_ROUTE_PAGE_PROPS }),
  ]);
  const result = await readCollectionsPage({ tabId: 1, exec });
  assert.deepEqual(result, { nextData: STALE_NEXT_DATA, entries: ENTRIES, found: true });
});

test("readCollectionsPage: a route-supplied EMPTY favoritesList array is trusted over a non-empty stale inline one", async () => {
  const { exec } = stubExec([
    pageResult({ nextData: NEXT_DATA, routePageProps: { favoritesList: [] } }),
  ]);
  const result = await readCollectionsPage({ tabId: 1, exec });
  assert.deepEqual(result, { nextData: NEXT_DATA, entries: [], found: true });
});

test("readCollectionsPage: route unreachable (routePageProps null) falls back to the inline snapshot", async () => {
  const { exec } = stubExec([pageResult({ nextData: NEXT_DATA, routePageProps: null })]);
  const result = await readCollectionsPage({ tabId: 1, exec });
  assert.deepEqual(result, { nextData: NEXT_DATA, entries: ENTRIES, found: true });
});

test("readCollectionsPage: route missing the favoritesList shape also falls back to the inline snapshot", async () => {
  const { exec } = stubExec([
    pageResult({ nextData: NEXT_DATA, routePageProps: { someOtherField: 1 } }),
  ]);
  const result = await readCollectionsPage({ tabId: 1, exec });
  assert.deepEqual(result, { nextData: NEXT_DATA, entries: ENTRIES, found: true });
});

test("readCollectionsPage: both route and inline missing a favoritesList array returns found:false, entries: []", async () => {
  const { exec } = stubExec([pageResult({ nextData: null, routePageProps: null })]);
  const result = await readCollectionsPage({ tabId: 1, exec });
  assert.deepEqual(result, { nextData: null, entries: [], found: false });
});

test("readCollectionsPage: extracts entries from a real favoritesList shape via the inline snapshot", async () => {
  const { exec } = stubExec([pageResult({ nextData: NEXT_DATA })]);
  const result = await readCollectionsPage({ tabId: 1, exec });
  assert.deepEqual(result.entries, ENTRIES);
  assert.equal(result.found, true);
});

test("hashCollectionsPayload: stable for the same entries, differs for different entries", async () => {
  const a1 = await hashCollectionsPayload(ENTRIES);
  const a2 = await hashCollectionsPayload(ENTRIES);
  const b = await hashCollectionsPayload([ENTRIES[0]]);
  assert.equal(a1, a2);
  assert.notEqual(a1, b);
  assert.match(a1, /^[0-9a-f]{64}$/);
});

test("hashCollectionsPayload: matches hashing the JSON-stringified entries directly (reuses courier's hashToken)", async () => {
  const expected = await hashToken(JSON.stringify(ENTRIES));
  assert.equal(await hashCollectionsPayload(ENTRIES), expected);
});

test("hashCollectionsPayload: null/undefined entries hash the same as an empty array", async () => {
  const empty = await hashCollectionsPayload([]);
  assert.equal(await hashCollectionsPayload(null), empty);
  assert.equal(await hashCollectionsPayload(undefined), empty);
});

test("shouldPushCollections: true when lastHash is null (first sync)", async () => {
  assert.equal(await shouldPushCollections(ENTRIES, null), true);
});

test("shouldPushCollections: false when entries' hash matches lastHash", async () => {
  const hash = await hashCollectionsPayload(ENTRIES);
  assert.equal(await shouldPushCollections(ENTRIES, hash), false);
});

test("shouldPushCollections: true when a collection's count changed (membership edit) even with the same list_id set", async () => {
  const before = [{ list_id: "1", title: "A", slug: null, count: 3, is_default: false }];
  const after = [{ list_id: "1", title: "A", slug: null, count: 4, is_default: false }];
  const beforeHash = await hashCollectionsPayload(before);
  assert.equal(await shouldPushCollections(after, beforeHash), true);
});

test("shouldPersistHash: false when the run left one or more collections unreadable", () => {
  assert.equal(
    shouldPersistHash({ collections: 2, items: 1, unreadable: ["Default Collection"] }),
    false,
  );
});

test("shouldPersistHash: true on a fully-clean run (nothing unreadable)", () => {
  assert.equal(shouldPersistHash({ collections: 2, items: 2, unreadable: [] }), true);
});

test("shouldPersistHash: true when the input predates F2 and carries no partial field at all (backward-compatible default)", () => {
  assert.equal(shouldPersistHash({ collections: 2, items: 2, unreadable: [] }), true);
});

test("shouldPersistHash: false when the run left one or more collections partial (F2), even with nothing unreadable", () => {
  assert.equal(
    shouldPersistHash({ collections: 2, items: 2, unreadable: [], partial: ["Big Collection"] }),
    false,
  );
});

test("shouldPersistHash: false when a run has BOTH unreadable and partial collections", () => {
  assert.equal(
    shouldPersistHash({
      collections: 3,
      items: 2,
      unreadable: ["Default Collection"],
      partial: ["Big Collection"],
    }),
    false,
  );
});

test("shouldPersistHash: true when unreadable and partial are both explicitly empty", () => {
  assert.equal(shouldPersistHash({ collections: 2, items: 2, unreadable: [], partial: [] }), true);
});

// readCollectionDetailPage / syncCollectionDetail (M11): the guaranteed-
// correct single-collection sync, driven off the collection DETAIL page the
// user is actually looking at (ground truth:
// `https://makerworld.com/en/collections/18925823-esp32`) -- no route-
// guessing, no anchor-hunting.

const DETAIL_URL = "https://makerworld.com/en/collections/18925823-esp32";
const DETAIL_URL_NO_SLUG = "https://makerworld.com/collections/18925823";

/** A queued `stubExec` step for the detail-page read exec call --
 * `readCollectionsDataInPage`'s `{nextData, routePageProps}` shape (reused
 * verbatim from the collections LIST page read). */
function detailPageResult({ nextData = null, routePageProps = null } = {}) {
  return { result: { nextData, routePageProps } };
}

test("readCollectionDetailPage: happy path -- parses id/slug from the URL, finds items + title + count via the fresh route pageProps", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: {
        favoritesInfo: { id: 18925823, title: "ESP32", designCnt: 1 },
        designs: [{ id: 1, title: "Item A" }],
      },
    }),
  ]);

  const page = await readCollectionDetailPage({ tabId: 7, url: DETAIL_URL, exec });

  assert.deepEqual(page.parsed, { id: "18925823", slug: "esp32" });
  assert.equal(page.found, true);
  assert.equal(page.title, "ESP32");
  assert.equal(page.count, 1); // I1: wired through from findCollectionTitleIn's derived count
  assert.deepEqual(page.items, [
    {
      external_id: "1",
      title: "Item A",
      url: "https://makerworld.com/models/1",
      author: null,
      thumbnail_url: null,
    },
  ]);
});

test("readCollectionDetailPage: count is null when the matched collection-info object has no numeric designCnt/count field", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: {
        favoritesInfo: { id: 18925823, title: "ESP32" },
        designs: [{ id: 1, title: "Item A" }],
      },
    }),
  ]);

  const page = await readCollectionDetailPage({ tabId: 7, url: DETAIL_URL, exec });

  assert.equal(page.title, "ESP32");
  assert.equal(page.count, null);
});

test("readCollectionDetailPage: found:false (with parsed id/slug still returned) when no design list is recognizable", async () => {
  const { exec } = stubExec([detailPageResult({ routePageProps: { unrelated: "noise" } })]);

  const page = await readCollectionDetailPage({ tabId: 7, url: DETAIL_URL, exec });

  assert.deepEqual(page.parsed, { id: "18925823", slug: "esp32" });
  assert.equal(page.found, false);
  assert.deepEqual(page.items, []);
  assert.equal(page.title, null);
  assert.equal(page.count, null);
});

test("readCollectionDetailPage: title is null when no collection-info shape is recognizable (items-only fallback)", async () => {
  const { exec } = stubExec([detailPageResult({ routePageProps: { designs: [{ id: 1, title: "Item A" }] } })]);

  const page = await readCollectionDetailPage({ tabId: 7, url: DETAIL_URL, exec });

  assert.equal(page.found, true);
  assert.equal(page.title, null);
});

test("readCollectionDetailPage: returns null for a URL that isn't a collection detail page", async () => {
  const { exec } = stubExec([]);
  const page = await readCollectionDetailPage({ tabId: 7, url: COLLECTIONS_URL, exec });
  assert.equal(page, null);
});

test("readCollectionDetailPage: returns null when the page read throws", async () => {
  const { exec } = stubExec([{ throws: "no such tab" }]);
  const page = await readCollectionDetailPage({ tabId: 7, url: DETAIL_URL, exec });
  assert.equal(page, null);
});

test("hashCollectionItemsPayload: stable for the same items, differs when the id set differs", async () => {
  const a1 = await hashCollectionItemsPayload([ITEM_A, ITEM_B]);
  const a2 = await hashCollectionItemsPayload([ITEM_A, ITEM_B]);
  const b = await hashCollectionItemsPayload([ITEM_A]);
  assert.equal(a1, a2);
  assert.notEqual(a1, b);
  assert.match(a1, /^[0-9a-f]{64}$/);
});

test("hashCollectionItemsPayload: hashes ids only -- a title/author/thumbnail change alone doesn't change the hash", async () => {
  const before = await hashCollectionItemsPayload([{ external_id: "1", title: "Old Title" }]);
  const after = await hashCollectionItemsPayload([{ external_id: "1", title: "New Title" }]);
  assert.equal(before, after);
});

test("hashCollectionItemsPayload: null/undefined/empty items all hash the same", async () => {
  const empty = await hashCollectionItemsPayload([]);
  assert.equal(await hashCollectionItemsPayload(null), empty);
  assert.equal(await hashCollectionItemsPayload(undefined), empty);
});

test("findLastCollectionItemsHash: finds a matching listId, null when absent or the array is empty/missing", () => {
  const entries = [
    { listId: "1", hash: "aaa" },
    { listId: "2", hash: "bbb" },
  ];
  assert.equal(findLastCollectionItemsHash(entries, "2"), "bbb");
  assert.equal(findLastCollectionItemsHash(entries, "99"), null);
  assert.equal(findLastCollectionItemsHash([], "1"), null);
  assert.equal(findLastCollectionItemsHash(null, "1"), null);
  assert.equal(findLastCollectionItemsHash(undefined, "1"), null);
});

test("upsertCollectionItemsHash: appends a new listId at the end", () => {
  const result = upsertCollectionItemsHash([{ listId: "1", hash: "aaa" }], "2", "bbb");
  assert.deepEqual(result, [
    { listId: "1", hash: "aaa" },
    { listId: "2", hash: "bbb" },
  ]);
});

test("upsertCollectionItemsHash: an existing listId is updated AND moved to the end (most-recently-synced)", () => {
  const result = upsertCollectionItemsHash(
    [
      { listId: "1", hash: "aaa" },
      { listId: "2", hash: "bbb" },
    ],
    "1",
    "aaa-updated",
  );
  assert.deepEqual(result, [
    { listId: "2", hash: "bbb" },
    { listId: "1", hash: "aaa-updated" },
  ]);
});

test("upsertCollectionItemsHash: prunes to the last 50 by default", () => {
  const entries = Array.from({ length: 50 }, (_, i) => ({ listId: String(i), hash: `h${i}` }));
  const result = upsertCollectionItemsHash(entries, "50", "h50");
  assert.equal(result.length, 50);
  assert.equal(result[0].listId, "1"); // the oldest ("0") was pruned
  assert.equal(result[result.length - 1].listId, "50");
});

test("upsertCollectionItemsHash: honors a custom limit", () => {
  const entries = [
    { listId: "1", hash: "a" },
    { listId: "2", hash: "b" },
  ];
  const result = upsertCollectionItemsHash(entries, "3", "c", 2);
  assert.deepEqual(result, [
    { listId: "2", hash: "b" },
    { listId: "3", hash: "c" },
  ]);
});

test("upsertCollectionItemsHash: recency order survives canonical-numeric-looking listIds (the array-not-object reason)", () => {
  // If this were a plain object keyed by listId, a JS engine would silently
  // reorder these INTEGER-like keys to ascending numeric order ("2", "10",
  // "9999999") regardless of insertion order -- ruining "last N by
  // recency". The array form must preserve insertion order exactly.
  let entries = [];
  entries = upsertCollectionItemsHash(entries, "9999999", "h1");
  entries = upsertCollectionItemsHash(entries, "2", "h2");
  entries = upsertCollectionItemsHash(entries, "10", "h3");
  assert.deepEqual(
    entries.map((e) => e.listId),
    ["9999999", "2", "10"],
  );
});

// `shouldPushCollectionItems` (the boolean-only "has this listId's items
// hash changed" check) was removed (M3, dead export) -- `background.js`'s
// `runCollectionDetailSync` already needs the RAW computed hash value for
// the later throttle-persist step, so it keeps its own inline
// `hashCollectionItemsPayload` + `findLastCollectionItemsHash` compare
// (covered directly by the tests above) rather than routing through a
// boolean wrapper that would have to be reshaped to hand the hash back out
// anyway.
