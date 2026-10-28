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

test("syncCollections: happy path -- pushes the list, reads+pushes each collection's items via the constructed ground-truth data route, returns the summary", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: NEXT_DATA }),
    anchorResult([]), // no anchor links found on the page -- falls to the constructed pathname
    designsRoute({ designs: [{ id: 1, title: "Item A" }] }),
    designsRoute({ designs: [{ id: 2, title: "Item B" }] }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({
    tabId: 7,
    url: COLLECTIONS_URL,
    exec,
    api,
    report,
  });

  assert.deepEqual(summary, { collections: 2, items: 2, unreadable: [], partial: [] });
  assert.deepEqual(apiCalls.pushCollections[0], { site: "makerworld", collections: ENTRIES });
  assert.equal(apiCalls.pushCollectionItems.length, 2);
  assert.equal(apiCalls.pushCollectionItems[0].listId, "2155987");
  assert.deepEqual(apiCalls.pushCollectionItems[0].items, [
    {
      external_id: "1",
      title: "Item A",
      url: "https://makerworld.com/models/1",
      author: null,
      thumbnail_url: null,
    },
  ]);
  assert.equal(apiCalls.pushCollectionItems[1].listId, "18925823");
  // Page read + one anchor-collect exec call + one constructed-pathname
  // data-route fetch per collection -- no fallback calls needed since a
  // primary source succeeded for both.
  assert.equal(execCalls.length, 4);
  assert.deepEqual(execCalls[2].args, [BUILD_ID, CONSTRUCTED_PATHNAME_2155987]);
  assert.deepEqual(execCalls[3].args, [BUILD_ID, CONSTRUCTED_PATHNAME_18925823]);
  const last = reportCalls[reportCalls.length - 1];
  assert.equal(last.text, "Synced 2 collections (2 items).");
  assert.equal(last.kind, "ok");
});

test("syncCollections: an anchor-derived pathname is tried FIRST and preferred over the constructed one", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: ONE_ENTRY_NEXT_DATA }),
    // A real link to the collection was found on the page -- differs from
    // the constructed pathname (no slug here) so the test can tell which
    // one was actually fetched.
    anchorResult(["/en/collections/2155987"]),
    designsRoute({ designs: [{ id: 1, title: "Item A" }] }),
    // No further step queued -- if the code also tried the constructed
    // pathname, stubExec would throw "no step queued" and fail this test.
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 1, unreadable: [], partial: [] });
  assert.equal(execCalls.length, 3);
  assert.deepEqual(execCalls[2].args, [BUILD_ID, "/en/collections/2155987"]);
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 1);
});

test("syncCollections: an anchor pathname that 404s falls back to the constructed pathname", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: ONE_ENTRY_NEXT_DATA }),
    anchorResult(["/en/collections/2155987-wrong-slug"]),
    { result: null }, // anchor pathname 404s
    designsRoute({ designs: [{ id: 1, title: "Item A" }] }), // constructed pathname succeeds
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 1, unreadable: [], partial: [] });
  assert.equal(execCalls.length, 4);
  assert.deepEqual(execCalls[2].args, [BUILD_ID, "/en/collections/2155987-wrong-slug"]);
  assert.deepEqual(execCalls[3].args, [BUILD_ID, CONSTRUCTED_PATHNAME_2155987]);
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 1);
});

test("syncCollections: route+inline both unreadable reports the hard-refresh message and pushes nothing", async () => {
  // Neither the data route nor the inline __NEXT_DATA__ carried a
  // favoritesList array at all -- the stale-snapshot/unreachable-route
  // failure mode from the live bug report, distinct from a genuinely-empty
  // account (next test).
  const { exec } = stubExec([pageResult({ nextData: null, routePageProps: null })]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report }),
    /Couldn't read your collections from this page/,
  );

  assert.equal(apiCalls.pushCollections.length, 0);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  assert.deepEqual(reportCalls[reportCalls.length - 1], {
    text: "Couldn't read your collections from this page — try a hard refresh (Ctrl+Shift+R) and click again.",
    kind: "error",
  });
});

test("syncCollections: a favoritesList array WAS found but is genuinely empty reports the empty-account message", async () => {
  const { exec } = stubExec([
    pageResult({ nextData: { props: { pageProps: { favoritesList: [] } } } }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report }),
    /No collections in your MakerWorld account yet\./,
  );

  assert.equal(apiCalls.pushCollections.length, 0);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  assert.deepEqual(reportCalls[reportCalls.length - 1], {
    text: "No collections in your MakerWorld account yet.",
    kind: "error",
  });
});

test("syncCollections: a collection unreadable from EVERY source is skipped and listed, with a diagnostic appended; a collection whose primary source succeeds never calls the fallback", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: NEXT_DATA }),
    anchorResult([]), // no anchor links found
    { throws: "data route unreachable" }, // collection 1 constructed-pathname primary throws
    { result: [{ hits: [], total: 0 }] }, // collection 1 fallback: empty
    designsRoute({ designs: [{ id: 2, title: "Item B" }] }), // collection 2 primary succeeds
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, {
    collections: 2,
    items: 1,
    unreadable: ["Default Collection"],
    partial: [],
  });
  // Only the readable collection's items were pushed.
  assert.equal(apiCalls.pushCollectionItems.length, 1);
  assert.equal(apiCalls.pushCollectionItems[0].listId, "18925823");
  assert.equal(execCalls.length, 5); // page + anchor-collect + (primary throw + fallback) + primary-only
  const last = reportCalls[reportCalls.length - 1];
  assert.equal(
    last.text,
    "Synced 2 collections (1 items; 1 collection unreadable). " +
      'Diagnostic for "Default Collection" -- anchor link not found; ' +
      `constructed (${CONSTRUCTED_PATHNAME_2155987}): route unreachable; fallback: 0 items.`,
  );
  assert.equal(last.kind, "ok");
});

test("syncCollections: a collection whose item push is rejected is also listed as unreadable", async () => {
  const { exec } = stubExec([
    pageResult({ nextData: NEXT_DATA }),
    anchorResult([]),
    designsRoute({ designs: [{ id: 1, title: "Item A" }] }),
    designsRoute({ designs: [{ id: 2, title: "Item B" }] }),
  ]);
  const { api, calls: apiCalls } = stubApi({
    pushCollectionItems: [errResult("collection not found"), undefined],
  });
  const { report } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 2, items: 1, unreadable: ["Default Collection"], partial: [] });
  assert.equal(apiCalls.pushCollectionItems.length, 2);
});

test("syncCollections: items still sync via the handle-free primary source even when the URL has no /@handle/collections shape", async () => {
  // extractHandle returns null here (URL doesn't match /@handle/collections,
  // NEXT_DATA has no recognizable handle field) -- the primary data-route
  // sources don't need a handle at all, only buildId + (an anchor pathname
  // or the constructed one), both independent of the handle. The
  // constructed pathname's locale prefix is also independent of this URL's
  // own path shape -- `OTHER_URL` has no locale segment, so no prefix.
  const OTHER_URL = "https://makerworld.com/some/other/page";
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: NEXT_DATA }),
    anchorResult([]),
    designsRoute({ designs: [{ id: 1, title: "Item A" }] }),
    designsRoute({ designs: [{ id: 2, title: "Item B" }] }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: OTHER_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 2, items: 2, unreadable: [], partial: [] });
  assert.equal(apiCalls.pushCollectionItems.length, 2);
  assert.equal(execCalls.length, 4); // no fallback calls -- a handle was never needed
  assert.deepEqual(execCalls[2].args, [BUILD_ID, "/collections/2155987-default-collection"]);
  assert.deepEqual(execCalls[3].args, [BUILD_ID, "/collections/18925823-esp32"]);
  assert.equal(reportCalls[reportCalls.length - 1].text, "Synced 2 collections (2 items).");
});

test("syncCollections: no buildId (primary + anchor-collect both skipped) and no handle (fallback skipped) leaves every collection unreadable but still succeeds with a truthful count, diagnostic for the first only", async () => {
  const NEXT_DATA_NO_BUILD = {
    props: { pageProps: { favoritesList: NEXT_DATA.props.pageProps.favoritesList } },
  };
  const OTHER_URL = "https://makerworld.com/some/other/page";
  const { exec, calls: execCalls } = stubExec([pageResult({ nextData: NEXT_DATA_NO_BUILD })]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: OTHER_URL, exec, api, report });

  assert.deepEqual(summary, {
    collections: 2,
    items: 0,
    unreadable: ["Default Collection", "ESP32"],
    partial: [],
  });
  assert.equal(apiCalls.pushCollections.length, 1);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  // Only the page read -- no buildId means neither the anchor-collect call
  // nor any per-collection data-route call was even attempted, and no
  // handle means the /api/v1 fallback was skipped too.
  assert.equal(execCalls.length, 1);
  assert.equal(
    reportCalls[reportCalls.length - 1].text,
    "Synced 2 collections (0 items; 2 collections unreadable). " +
      'Diagnostic for "Default Collection" -- anchor link not found; fallback: skipped (no handle).',
  );
});

test("syncCollections: an unreadable page reports and rejects with 'Couldn't read this page.', no pushes", async () => {
  const { exec } = stubExec([{ throws: "executeScript failed" }]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report }),
    /Couldn't read this page\./,
  );

  assert.equal(apiCalls.pushCollections.length, 0);
  assert.deepEqual(reportCalls[reportCalls.length - 1], {
    text: "Couldn't read this page.",
    kind: "error",
  });
});

test("syncCollections: a rejected list push reports the server's error and rejects, no item fetches", async () => {
  const { exec, calls: execCalls } = stubExec([pageResult({ nextData: NEXT_DATA })]);
  const { api } = stubApi({ pushCollections: errResult("collections: at most 200 entries") });
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report }),
    /collections: at most 200 entries/,
  );

  assert.equal(execCalls.length, 1); // only the page read -- no item fetches
  assert.deepEqual(reportCalls[reportCalls.length - 1], {
    text: "collections: at most 200 entries",
    kind: "error",
  });
});

test("syncCollections: a rejected list push with no error text falls back to a generic message", async () => {
  const { exec } = stubExec([pageResult({ nextData: NEXT_DATA })]);
  const { api } = stubApi({ pushCollections: { ok: false, status: 500, data: null, error: "" } });
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report }),
    /Something went wrong\./,
  );
  assert.equal(reportCalls[reportCalls.length - 1].text, "Something went wrong.");
});

test("syncCollections: an injected `page` skips the module's own page read entirely (the anchor-collect exec call still runs)", async () => {
  const { exec, calls: execCalls } = stubExec([
    anchorResult([]),
    designsRoute({ designs: [{ id: 1, title: "Item A" }] }),
    designsRoute({ designs: [{ id: 2, title: "Item B" }] }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollections({
    tabId: 7,
    url: COLLECTIONS_URL,
    exec,
    api,
    report,
    page: { nextData: NEXT_DATA, entries: ENTRIES, found: true },
  });

  assert.deepEqual(summary, { collections: 2, items: 2, unreadable: [], partial: [] });
  // The anchor-collect exec call plus the two constructed-pathname fetches
  // -- no exec call for the page read itself (that's what `page` skips).
  assert.equal(execCalls.length, 3);
  assert.deepEqual(apiCalls.pushCollections[0].collections, ENTRIES);
});

test("syncCollections item-source preference: the constructed ground-truth route is preferred and the api fallback is never called when it yields designs", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: ONE_ENTRY_NEXT_DATA }),
    anchorResult([]),
    designsRoute({ designs: [{ id: 1, title: "Item A" }] }),
    // No fourth step queued -- if the code also called the api fallback,
    // stubExec would throw "no step queued" and fail this test.
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 1, unreadable: [], partial: [] });
  assert.equal(execCalls.length, 3);
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 1);
});

test("syncCollections item-source preference: an empty/404 constructed route falls back to the api endpoint", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: ONE_ENTRY_NEXT_DATA }),
    anchorResult([]),
    { result: null }, // constructed route 404s/parses empty -- fetchCollectionDataRouteInPage returns null
    { result: [{ hits: [{ id: 9, title: "Fallback Item" }], total: 1 }] }, // api fallback
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 1, unreadable: [], partial: [] });
  assert.equal(execCalls.length, 4);
  assert.deepEqual(apiCalls.pushCollectionItems[0].items, [
    {
      external_id: "9",
      title: "Fallback Item",
      url: "https://makerworld.com/models/9",
      author: null,
      thumbnail_url: null,
    },
  ]);
});

test("syncCollections item-source preference: every source empty leaves the collection unreadable with nothing pushed for it, diagnostic scrubbed to key names only", async () => {
  const { exec } = stubExec([
    pageResult({ nextData: ONE_ENTRY_NEXT_DATA }),
    anchorResult([]),
    designsRoute({ someOtherField: "value that must never leak", count: 3 }), // constructed: ok, but no design array
    { result: [{ hits: [], total: 0 }] }, // fallback empty
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 0, unreadable: ["Default Collection"], partial: [] });
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  const last = reportCalls[reportCalls.length - 1];
  // The diagnostic names the pageProps KEYS ("someOtherField, count") but
  // must never leak the leaked-looking VALUE ("value that must never leak").
  assert.ok(last.text.includes("pageProps keys: someOtherField, count"));
  assert.ok(!last.text.includes("value that must never leak"));
});

test("syncCollections: primary item source tolerates a deep-scan-discovered design array under an unrecognized key, normalizing 'name' to 'title'", async () => {
  const { exec } = stubExec([
    pageResult({ nextData: ONE_ENTRY_NEXT_DATA }),
    anchorResult([]),
    designsRoute({ weirdKey: [{ id: 77, name: "Deep Item" }] }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 1, unreadable: [], partial: [] });
  assert.deepEqual(apiCalls.pushCollectionItems[0].items, [
    {
      external_id: "77",
      title: "Deep Item",
      url: "https://makerworld.com/models/77",
      author: null,
      thumbnail_url: null,
    },
  ]);
  // Logs which pageProps key matched, for diagnosability.
  assert.ok(reportCalls.some((c) => c.text.includes("weirdKey")));
});

// F3 hardening: `readCollectionItemsFromDataRoute` distinguishes "a trusted
// design list was located and it's genuinely empty" (`found: true`) from
// "no recognizable design list at all" (`found: false`) -- only the latter
// should fall back to the `/api/v1` endpoint and, if that's also empty, get
// flagged unreadable.

test("syncCollections F3: primary data route reports a trusted-but-EMPTY items array -- pushes nothing for it, does NOT mark it unreadable, never tries the fallback, and the hash may still advance", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: ONE_ENTRY_NEXT_DATA }),
    anchorResult([]),
    designsRoute({ designs: [] }), // trusted-empty via the named "designs" key
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 0, unreadable: [], partial: [] });
  assert.equal(apiCalls.pushCollectionItems.length, 0); // nothing pushed -- genuinely empty
  // Page read + anchor-collect + the one constructed-pathname route fetch --
  // no fallback exec call at all.
  assert.equal(execCalls.length, 3);
  assert.equal(shouldPersistHash(summary), true); // not unreadable, not partial -- hash may advance
  assert.equal(reportCalls[reportCalls.length - 1].text, "Synced 1 collections (0 items).");
});

// F2 hardening: the primary data route can silently truncate a large
// collection's item array. Dedicated fixture -- ONE collection whose known
// `designCnt` (5) deliberately exceeds what the stubbed reads return, unlike
// `NEXT_DATA`/`ENTRIES` above (kept mismatch-free on purpose, see their own
// doc) -- so the truncation-recovery logic has something real to exercise.
const TRUNCATED_NEXT_DATA = {
  buildId: BUILD_ID,
  props: {
    pageProps: {
      favoritesList: [
        {
          id: 555,
          title: "Big Collection",
          slug: "big-collection",
          isDefault: false,
          designCnt: 5,
          status: 1,
        },
      ],
    },
  },
};

test("syncCollections F2: a primary read shorter than the collection's known count triggers the paged /api/v1 fallback, and a LONGER fallback result replaces it (not partial)", async () => {
  const { exec, calls: execCalls } = stubExec([
    pageResult({ nextData: TRUNCATED_NEXT_DATA }),
    anchorResult([]),
    designsRoute({ designs: [{ id: 1, title: "Item 1" }, { id: 2, title: "Item 2" }] }), // primary: 2 of 5
    {
      result: [
        {
          hits: [
            { id: 1, title: "Item 1" },
            { id: 2, title: "Item 2" },
            { id: 3, title: "Item 3" },
            { id: 4, title: "Item 4" },
            { id: 5, title: "Item 5" },
          ],
          total: 5,
        },
      ],
    }, // fallback: the full 5
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, { collections: 1, items: 5, unreadable: [], partial: [] });
  assert.equal(execCalls.length, 4); // page + anchor-collect + primary + fallback
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 5); // the fallback's fuller set was used
  assert.equal(shouldPersistHash(summary), true);
  assert.equal(reportCalls[reportCalls.length - 1].text, "Synced 1 collections (5 items).");
});

test("syncCollections F2: a fallback that comes back no better than the primary is discarded -- the primary's (still-short) items are kept, pushed, and the collection is marked partial", async () => {
  const { exec } = stubExec([
    pageResult({ nextData: TRUNCATED_NEXT_DATA }),
    anchorResult([]),
    designsRoute({
      designs: [
        { id: 1, title: "Item 1" },
        { id: 2, title: "Item 2" },
        { id: 3, title: "Item 3" },
      ],
    }), // primary: 3 of 5
    { result: [{ hits: [{ id: 1, title: "Item 1" }], total: 1 }] }, // fallback: worse (1)
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary, {
    collections: 1,
    items: 3,
    unreadable: [],
    partial: ["Big Collection"],
  });
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 3); // primary's items, not the fallback's
  assert.equal(
    reportCalls[reportCalls.length - 1].text,
    "Synced 1 collections (3 items; 1 collection partial).",
  );
  // A partial collection must suppress the background auto-sync hash
  // persist just like an unreadable one -- otherwise the next visit never
  // retries it.
  assert.equal(shouldPersistHash(summary), false);
});

test("syncCollections F2: multiple partial collections are all listed, and the status line pluralizes correctly", async () => {
  const TWO_TRUNCATED_NEXT_DATA = {
    buildId: BUILD_ID,
    props: {
      pageProps: {
        favoritesList: [
          {
            id: 555,
            title: "Big Collection",
            slug: "big-collection",
            isDefault: false,
            designCnt: 5,
            status: 1,
          },
          {
            id: 556,
            title: "Big Collection 2",
            slug: "big-collection-2",
            isDefault: false,
            designCnt: 4,
            status: 1,
          },
        ],
      },
    },
  };
  const { exec } = stubExec([
    pageResult({ nextData: TWO_TRUNCATED_NEXT_DATA }),
    anchorResult([]),
    designsRoute({ designs: [{ id: 1, title: "Item 1" }] }), // 1 of 5
    { result: [{ hits: [{ id: 1, title: "Item 1" }], total: 1 }] }, // fallback no better
    designsRoute({ designs: [{ id: 2, title: "Item 2" }] }), // 1 of 4
    { result: [{ hits: [{ id: 2, title: "Item 2" }], total: 1 }] }, // fallback no better
  ]);
  const { api } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollections({ tabId: 7, url: COLLECTIONS_URL, exec, api, report });

  assert.deepEqual(summary.partial, ["Big Collection", "Big Collection 2"]);
  assert.equal(summary.unreadable.length, 0);
  assert.equal(
    reportCalls[reportCalls.length - 1].text,
    "Synced 2 collections (2 items; 2 collections partial).",
  );
  assert.equal(shouldPersistHash(summary), false);
});

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

// describeUnreadableCollection (M11): the diagnostic-line formatter, unit-
// tested directly in addition to the integration coverage above.

test("describeUnreadableCollection: composes anchor status, every attempt, and the fallback outcome", () => {
  const text = describeUnreadableCollection("My Collection", {
    anchorPathname: "/en/collections/18925823-esp32",
    attempts: [
      { label: "anchor", pathname: "/en/collections/18925823-esp32", diagnostic: "route 404 or unavailable" },
      { label: "constructed", pathname: "/en/collections/18925823", diagnostic: "route unreachable" },
    ],
    fallback: "0 items",
  });
  assert.equal(
    text,
    'Diagnostic for "My Collection" -- anchor link found (/en/collections/18925823-esp32); ' +
      "anchor (/en/collections/18925823-esp32): route 404 or unavailable; " +
      "constructed (/en/collections/18925823): route unreachable; fallback: 0 items.",
  );
});

test("describeUnreadableCollection: no anchor found and no attempts at all (e.g. no buildId)", () => {
  const text = describeUnreadableCollection("My Collection", {
    anchorPathname: null,
    attempts: [],
    fallback: "skipped (no handle)",
  });
  assert.equal(
    text,
    'Diagnostic for "My Collection" -- anchor link not found; fallback: skipped (no handle).',
  );
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

test("readCollectionDetailPage: falls back to the inline __NEXT_DATA__ pageProps when the route is unreachable", async () => {
  const { exec } = stubExec([
    detailPageResult({
      nextData: { props: { pageProps: { designs: [{ id: 1, title: "Item A" }] } } },
      routePageProps: null,
    }),
  ]);

  const page = await readCollectionDetailPage({ tabId: 7, url: DETAIL_URL, exec });

  assert.equal(page.found, true);
  assert.equal(page.items.length, 1);
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

test("syncCollectionDetail: happy path -- derivable count == items read (I1 confirmed-complete), pushes both a collections upsert entry and the items, reports by title", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: {
        favoritesInfo: { id: 18925823, title: "ESP32", designCnt: 1 },
        designs: [{ id: 1, title: "Item A" }],
      },
    }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report });

  assert.deepEqual(summary, {
    listId: "18925823",
    title: "ESP32",
    items: 1,
    partial: false,
    countDerived: true,
  });
  assert.deepEqual(apiCalls.pushCollections[0], {
    site: "makerworld",
    collections: [{ list_id: "18925823", title: "ESP32", slug: "esp32", count: 1, is_default: false }],
  });
  assert.equal(apiCalls.pushCollectionItems[0].listId, "18925823");
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 1);
  assert.equal(reportCalls[reportCalls.length - 1].text, 'Synced "ESP32": 1 item.');
});

// I1 (BLOCKING fix): syncCollectionDetail reads a collection's items off ONE
// un-paged SSR response with no paged fallback to retry a short read with --
// pushing a truncated read would silently shrink the backend's cached
// membership (pushCollectionItems is a replace-set). These three tests cover
// the derivable-count decision tree: TRUNCATED (skip everything), COMPLETE
// (push, stamp the derived count), and NOT DERIVABLE (push best-effort, but
// never assert a count the read didn't actually establish).

test("syncCollectionDetail I1: a derivable count greater than the items actually read is a TRUNCATED read -- pushes NOTHING, reports 'partial read', signals partial:true", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: {
        favoritesInfo: { id: 18925823, title: "ESP32", designCnt: 5 },
        designs: [{ id: 1, title: "Item A" }],
      },
    }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report });

  assert.deepEqual(summary, {
    listId: "18925823",
    title: "ESP32",
    items: 0,
    partial: true,
    countDerived: true,
  });
  assert.equal(apiCalls.pushCollections.length, 0);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  const last = reportCalls[reportCalls.length - 1];
  assert.equal(last.text, "Partial read (1 of 5) — skipped.");
  assert.equal(last.kind, "ok");
});

test("syncCollectionDetail I1: items read EXCEEDING the derivable count is not truncated (>= count) -- pushes normally, stamps the DERIVED count, not items.length", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: {
        favoritesInfo: { id: 18925823, title: "ESP32", designCnt: 1 },
        designs: [{ id: 1, title: "Item A" }, { id: 2, title: "Item B" }],
      },
    }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report });

  assert.deepEqual(summary, {
    listId: "18925823",
    title: "ESP32",
    items: 2,
    partial: false,
    countDerived: true,
  });
  assert.equal(apiCalls.pushCollections[0].collections[0].count, 1); // derived, not items.length (2)
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 2);
});

test("syncCollectionDetail I1: no derivable count at all -- pushes items + the collections entry (best-effort), but omits (null) count rather than asserting items.length", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: {
        favoritesInfo: { id: 18925823, title: "ESP32" }, // no designCnt/count field
        designs: [{ id: 1, title: "Item A" }, { id: 2, title: "Item B" }],
      },
    }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report });

  assert.deepEqual(summary, {
    listId: "18925823",
    title: "ESP32",
    items: 2,
    partial: false,
    countDerived: false,
  });
  assert.equal(apiCalls.pushCollections[0].collections[0].count, null);
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 2);
});

test("syncCollectionDetail: title NOT derivable -- pushes items only (no pushCollections call), reports by id", async () => {
  const { exec } = stubExec([
    detailPageResult({ routePageProps: { designs: [{ id: 1, title: "Item A" }, { id: 2, title: "Item B" }] } }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report });

  assert.deepEqual(summary, {
    listId: "18925823",
    title: null,
    items: 2,
    partial: false,
    countDerived: false,
  });
  assert.equal(apiCalls.pushCollections.length, 0);
  assert.equal(apiCalls.pushCollectionItems[0].listId, "18925823");
  assert.equal(reportCalls[reportCalls.length - 1].text, 'Synced "18925823": 2 items.');
});

test("syncCollectionDetail: zero items -- never pushes an empty membership set, but a derivable title still upserts the collection", async () => {
  const { exec } = stubExec([
    detailPageResult({ routePageProps: { favoritesInfo: { id: 18925823, title: "ESP32" }, designs: [] } }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  const summary = await syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report });

  assert.deepEqual(summary, {
    listId: "18925823",
    title: "ESP32",
    items: 0,
    partial: false,
    countDerived: false,
  });
  assert.equal(apiCalls.pushCollections.length, 1);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  assert.equal(reportCalls[reportCalls.length - 1].text, 'Synced "ESP32": 0 items.');
});

test("syncCollectionDetail: rejects with \"This isn't a collection page.\" for a non-detail URL, no pushes", async () => {
  const { exec } = stubExec([]);
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollectionDetail({ tabId: 7, url: COLLECTIONS_URL, exec, api, report }),
    /This isn't a collection page\./,
  );
  assert.equal(apiCalls.pushCollections.length, 0);
  assert.equal(reportCalls[reportCalls.length - 1].kind, "error");
});

test("syncCollectionDetail: rejects when no design list is recognizable on the page", async () => {
  const { exec } = stubExec([detailPageResult({ routePageProps: { unrelated: "noise" } })]);
  const { api } = stubApi();
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report }),
    /Couldn't read this collection's items from the page\./,
  );
  assert.equal(reportCalls[reportCalls.length - 1].kind, "error");
});

test("syncCollectionDetail: a rejected collections-upsert push reports the server's error and rejects, no item push", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: { favoritesInfo: { id: 18925823, title: "ESP32" }, designs: [{ id: 1, title: "Item A" }] },
    }),
  ]);
  const { api, calls: apiCalls } = stubApi({ pushCollections: errResult("nope") });
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report }), /nope/);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  assert.equal(reportCalls[reportCalls.length - 1].text, "nope");
});

test("syncCollectionDetail: a rejected items push reports the server's error and rejects", async () => {
  const { exec } = stubExec([
    detailPageResult({ routePageProps: { designs: [{ id: 1, title: "Item A" }] } }),
  ]);
  const { api } = stubApi({ pushCollectionItems: errResult("items nope") });
  const { report, calls: reportCalls } = stubReport();

  await assert.rejects(
    syncCollectionDetail({ tabId: 7, url: DETAIL_URL, exec, api, report }),
    /items nope/,
  );
  assert.equal(reportCalls[reportCalls.length - 1].text, "items nope");
});

test("syncCollectionDetail: an injected `page` skips the module's own page read entirely", async () => {
  const { exec, calls: execCalls } = stubExec([]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  const summary = await syncCollectionDetail({
    tabId: 7,
    url: DETAIL_URL,
    exec,
    api,
    report,
    page: {
      parsed: { id: "18925823", slug: "esp32" },
      pageProps: {},
      items: [
        {
          external_id: "1",
          title: "Item A",
          url: "https://makerworld.com/models/1",
          author: null,
          thumbnail_url: null,
        },
      ],
      title: "ESP32",
      found: true,
    },
  });

  assert.equal(execCalls.length, 0);
  assert.deepEqual(summary, {
    listId: "18925823",
    title: "ESP32",
    items: 1,
    partial: false,
    countDerived: false,
  });
  assert.equal(apiCalls.pushCollections.length, 1);
});

test("syncCollectionDetail: no slug in the URL is carried through to a null slug on the pushed entry", async () => {
  const { exec } = stubExec([
    detailPageResult({
      routePageProps: {
        favoritesInfo: { id: 18925823, title: "ESP32" },
        designs: [{ id: 1, title: "Item A" }],
      },
    }),
  ]);
  const { api, calls: apiCalls } = stubApi();
  const { report } = stubReport();

  await syncCollectionDetail({ tabId: 7, url: DETAIL_URL_NO_SLUG, exec, api, report });

  assert.equal(apiCalls.pushCollections[0].collections[0].slug, null);
});

// hashCollectionItemsPayload / findLastCollectionItemsHash /
// upsertCollectionItemsHash (M11): the per-collection throttle for the
// background detail-page auto-sync (`config.js`'s `lastCollectionItemsHash`).
// Deliberately an ARRAY of `{listId, hash}` pairs, not a plain object keyed
// by listId -- MakerWorld
// list ids are canonical-numeric-looking strings, and every JS engine
// silently reorders a plain object's INTEGER-like keys to ascending numeric
// order regardless of insertion order, which would break "prune to the last
// N by recency".

const ITEM_A = { external_id: "1", title: "Item A" };
const ITEM_B = { external_id: "2", title: "Item B" };

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
