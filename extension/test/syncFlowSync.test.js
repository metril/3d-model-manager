import { test } from "node:test";
import assert from "node:assert/strict";

import {
  describeUnreadableCollection,
  mapLimit,
  readCollectionDetailPage,
  readCollectionsPage,
  shouldPersistHash,
  syncCollectionDetail,
  syncCollections,
} from "../src/syncFlow.js";

const BUILD_ID = "b1";
const LIST_URL = "https://makerworld.com/en/@Terminalfoo/collections";
const DETAIL_URL = "https://makerworld.com/en/collections/18925823-esp32";

function nextDataFor(list) {
  return { buildId: BUILD_ID, props: { pageProps: { favoritesList: list } } };
}
const LIST = [
  { id: 1, title: "One", slug: "one", isDefault: true, designCnt: 1 },
  { id: 2, title: "Two", slug: "two", isDefault: false, designCnt: 1 },
];

/**
 * Path-routed exec stub: dispatches on the injected function's NAME so tests
 * don't depend on call order (collections are read concurrently).
 */
function routedExec({ page, anchors = [], routes = {} } = {}) {
  const calls = [];
  const exec = async (tabId, func, args = []) => {
    calls.push({ name: func.name, args });
    if (func.name === "readCollectionsDataInPage") {
      if (page instanceof Error) throw page;
      return page;
    }
    if (func.name === "collectCollectionAnchorPathnamesInPage") return anchors;
    if (func.name === "fetchCollectionDataRouteInPage") {
      const r = routes[args[1]];
      return r === undefined ? null : r;
    }
    throw new Error(`unexpected injected func ${func.name}`);
  };
  return { exec, calls };
}

const ok = (data = { ok: true }) => ({ ok: true, status: 200, data, error: null });
const err = (error) => ({ ok: false, status: 422, data: null, error });

function stubApi(overrides = {}) {
  const calls = { pushCollections: [], upsertCollection: [], pushCollectionItems: [] };
  const api = {
    pushCollections: async (site, collections) => {
      calls.pushCollections.push({ site, collections });
      return ok();
    },
    upsertCollection: async (site, entry) => {
      calls.upsertCollection.push({ site, entry });
      return ok();
    },
    pushCollectionItems: async (site, listId, items) => {
      calls.pushCollectionItems.push({ site, listId, items });
      return ok();
    },
    ...overrides,
  };
  return { api, calls };
}

function reporter() {
  const calls = [];
  return { report: (text, kind) => calls.push({ text, kind }), calls };
}

const design = (id, title = `Item ${id}`) => ({ id, title });
const route = (pageProps) => ({ pageProps });

function listPage(list = LIST) {
  return { nextData: nextDataFor(list), routePageProps: { favoritesList: list } };
}

// ---------------------------------------------------------------------------
// syncCollections
// ---------------------------------------------------------------------------

test("syncCollections: pushes the list, then each collection's items from its data route; no /api/v1 fallback exists", async () => {
  const { exec, calls } = routedExec({
    page: listPage(),
    routes: {
      "/en/collections/1-one": route({ designs: [design(10)] }),
      "/en/collections/2-two": route({ designs: [design(20)] }),
    },
  });
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reports } = reporter();

  const result = await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report });

  assert.deepEqual(result, { collections: 2, items: 2, unreadable: [], failed: [], partial: [] });
  assert.equal(apiCalls.pushCollections.length, 1);
  assert.deepEqual(
    apiCalls.pushCollectionItems.map((c) => c.listId).sort(),
    ["1", "2"],
  );
  assert.equal(reports.at(-1).text, "Synced 2 collections (2 items).");
  assert.equal(reports.at(-1).kind, "ok");
  // dead code is gone: only the three known injected functions are ever used
  const names = new Set(calls.map((c) => c.name));
  assert.deepEqual([...names].sort(), [
    "collectCollectionAnchorPathnamesInPage",
    "fetchCollectionDataRouteInPage",
    "readCollectionsDataInPage",
  ]);
});

test("syncCollections: an anchor pathname is tried first; a 404 anchor falls back to the constructed one", async () => {
  const { exec, calls } = routedExec({
    page: listPage([LIST[0]]),
    anchors: ["/en/collections/1-real-slug"],
    routes: { "/en/collections/1-one": route({ designs: [design(10)] }) },
  });
  const { api } = stubApi();
  await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report: () => {} });
  const fetched = calls.filter((c) => c.name === "fetchCollectionDataRouteInPage").map((c) => c.args[1]);
  assert.deepEqual(fetched, ["/en/collections/1-real-slug", "/en/collections/1-one"]);
});

test("syncCollections: with no locale on the page URL, tries /en/... then the locale-less route", async () => {
  const { exec, calls } = routedExec({
    page: listPage([LIST[0]]),
    routes: { "/collections/1-one": route({ designs: [design(10)] }) },
  });
  const { api, calls: apiCalls } = stubApi();
  await syncCollections({
    tabId: 1,
    url: "https://makerworld.com/@Terminalfoo/collections",
    exec,
    api,
    report: () => {},
  });
  const fetched = calls.filter((c) => c.name === "fetchCollectionDataRouteInPage").map((c) => c.args[1]);
  assert.deepEqual(fetched, ["/en/collections/1-one", "/collections/1-one"]);
  assert.equal(apiCalls.pushCollectionItems.length, 1);
});

test("syncCollections: an unreadable collection pushes nothing, is listed with a key-names-only diagnostic, and warns", async () => {
  const { exec } = routedExec({
    page: listPage([LIST[0]]),
    routes: { "/en/collections/1-one": route({ secretField: "SECRET VALUE", other: [] }) },
  });
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reports } = reporter();
  const result = await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report });
  assert.deepEqual(result.unreadable, ["One"]);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
  const last = reports.at(-1);
  assert.equal(last.kind, "warn");
  assert.match(last.text, /1 collection unreadable/);
  assert.match(last.text, /pageProps keys: secretField, other/);
  assert.doesNotMatch(last.text, /SECRET VALUE/);
  assert.equal(shouldPersistHash(result), false);
});

test("syncCollections: no buildId means nothing is readable and only the page read is injected", async () => {
  const { exec, calls } = routedExec({ page: { nextData: { props: { pageProps: { favoritesList: LIST } } }, routePageProps: null } });
  const { api } = stubApi();
  const result = await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report: () => {} });
  assert.deepEqual(result.unreadable, ["One", "Two"]);
  assert.deepEqual(calls.map((c) => c.name), ["readCollectionsDataInPage"]);
});

test("syncCollections: failedTitles (push rejected or threw) are separate from unreadableTitles, and one failure doesn't stop the rest", async () => {
  const { exec } = routedExec({
    page: listPage([...LIST, { id: 3, title: "Three", slug: "three", designCnt: 1 }]),
    routes: {
      "/en/collections/1-one": route({ designs: [design(10)] }),
      "/en/collections/2-two": route({ designs: [design(20)] }),
      "/en/collections/3-three": route({ designs: [design(30)] }),
    },
  });
  const { api, calls: apiCalls } = stubApi({
    pushCollectionItems: async (site, listId, items) => {
      apiCalls.pushCollectionItems.push({ listId });
      if (listId === "1") return err("rejected");
      if (listId === "2") throw new Error("boom");
      return ok();
    },
  });
  const { report, calls: reports } = reporter();
  const result = await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report });
  assert.deepEqual(result.failed, ["One", "Two"]);
  assert.deepEqual(result.unreadable, []);
  assert.equal(result.items, 1);
  assert.equal(apiCalls.pushCollectionItems.length, 3);
  assert.match(reports.at(-1).text, /2 collections failed to push/);
  assert.equal(shouldPersistHash(result), false);
});

test("syncCollections: trusted-empty list with count 0 pushes [] so removals propagate", async () => {
  const { exec } = routedExec({
    page: listPage([{ id: 1, title: "One", slug: "one", designCnt: 0 }]),
    routes: { "/en/collections/1-one": route({ designs: [] }) },
  });
  const { api, calls: apiCalls } = stubApi();
  const result = await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report: () => {} });
  assert.deepEqual(apiCalls.pushCollectionItems, [{ site: "makerworld", listId: "1", items: [] }]);
  assert.deepEqual(result.unreadable, []);
  assert.equal(shouldPersistHash(result), true);
});

test("syncCollections: an empty list whose count says non-empty is unreadable; with no count it is skipped; neither pushes", async () => {
  const { exec } = routedExec({
    page: listPage([
      { id: 1, title: "One", slug: "one", designCnt: 5 },
      { id: 2, title: "Two", slug: "two" },
    ]),
    routes: {
      "/en/collections/1-one": route({ designs: [] }),
      "/en/collections/2-two": route({ designs: [] }),
    },
  });
  const { api, calls: apiCalls } = stubApi();
  const result = await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report: () => {} });
  assert.deepEqual(result.unreadable, ["One"]);
  assert.equal(apiCalls.pushCollectionItems.length, 0);
});

test("syncCollections: partial uses the RAW array length vs. the count (and still pushes what it has)", async () => {
  const { exec } = routedExec({
    page: listPage([{ id: 1, title: "One", slug: "one", designCnt: 3 }]),
    routes: { "/en/collections/1-one": route({ designs: [design(1), design(2)] }) },
  });
  const { api, calls: apiCalls } = stubApi();
  const { report, calls: reports } = reporter();
  const result = await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report });
  assert.deepEqual(result.partial, ["One"]);
  assert.equal(apiCalls.pushCollectionItems[0].items.length, 2);
  assert.equal(reports.at(-1).kind, "warn");
  assert.equal(shouldPersistHash(result), false);
});

test("syncCollections: per-collection work runs with bounded concurrency (4)", async () => {
  const list = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, title: `C${i + 1}`, slug: `c${i + 1}`, designCnt: 1 }));
  const routes = Object.fromEntries(list.map((c) => [`/en/collections/${c.id}-${c.slug}`, route({ designs: [design(c.id)] })]));
  const { exec } = routedExec({ page: listPage(list), routes });
  let active = 0;
  let max = 0;
  const { api } = stubApi({
    pushCollectionItems: async () => {
      active++;
      max = Math.max(max, active);
      await new Promise((r) => setTimeout(r, 5));
      active--;
      return ok();
    },
  });
  await syncCollections({ tabId: 1, url: LIST_URL, exec, api, report: () => {} });
  assert.ok(max > 1 && max <= 4, `max concurrency was ${max}`);
});

test("syncCollections: rejects when the page is unreadable, or the list push is rejected", async () => {
  const { exec } = routedExec({ page: new Error("no tab") });
  const { api } = stubApi();
  await assert.rejects(syncCollections({ tabId: 1, url: LIST_URL, exec, api, report: () => {} }), /Couldn't read this page/);

  const { exec: exec2 } = routedExec({ page: listPage() });
  const { api: api2 } = stubApi({ pushCollections: async () => err("nope") });
  await assert.rejects(syncCollections({ tabId: 1, url: LIST_URL, exec: exec2, api: api2, report: () => {} }), /nope/);
});

test("syncCollections: the page read gets the expected pathname; a navigated-away read aborts", async () => {
  const { exec, calls } = routedExec({ page: { navigated: true, nextData: null, routePageProps: null } });
  assert.equal(await readCollectionsPage({ tabId: 1, url: LIST_URL, exec }), null);
  assert.deepEqual(calls[0].args, ["/en/@Terminalfoo/collections"]);
});

test("mapLimit: preserves order and never exceeds the limit", async () => {
  let active = 0;
  let max = 0;
  const out = await mapLimit([1, 2, 3, 4, 5, 6], 2, async (n) => {
    active++;
    max = Math.max(max, active);
    await new Promise((r) => setTimeout(r, 2));
    active--;
    return n * 2;
  });
  assert.deepEqual(out, [2, 4, 6, 8, 10, 12]);
  assert.equal(max, 2);
});

test("describeUnreadableCollection: composes anchor status and every attempt", () => {
  assert.equal(
    describeUnreadableCollection("X", {
      anchorPathname: "/en/collections/1-x",
      attempts: [{ label: "anchor", pathname: "/en/collections/1-x", diagnostic: "route 404 or unavailable" }],
    }),
    'Diagnostic for "X" -- anchor link found (/en/collections/1-x); anchor (/en/collections/1-x): route 404 or unavailable.',
  );
  assert.equal(
    describeUnreadableCollection("X", { anchorPathname: null, attempts: [] }),
    'Diagnostic for "X" -- anchor link not found.',
  );
});

// ---------------------------------------------------------------------------
// In-page functions (invoked directly with global stubs)
// ---------------------------------------------------------------------------

async function withPageGlobals({ pathname, fetchImpl, nextData }, fn) {
  const saved = { location: globalThis.location, document: globalThis.document, fetch: globalThis.fetch };
  globalThis.location = { pathname, origin: "https://makerworld.com" };
  globalThis.document = {
    getElementById: () => (nextData ? { textContent: JSON.stringify(nextData) } : null),
    querySelectorAll: () => [],
  };
  globalThis.fetch = fetchImpl;
  try {
    return await fn();
  } finally {
    globalThis.location = saved.location;
    globalThis.document = saved.document;
    globalThis.fetch = saved.fetch;
  }
}

async function captureInPageFunc(name, url = LIST_URL) {
  const { exec, calls } = routedExec({ page: listPage() });
  let captured;
  const spy = async (tabId, func, args) => {
    if (func.name === name) captured = func;
    return exec(tabId, func, args);
  };
  await readCollectionsPage({ tabId: 1, url, exec: spy });
  return captured;
}

test("in-page read: aborts with navigated:true when location.pathname differs from the expected one", async () => {
  const func = await captureInPageFunc("readCollectionsDataInPage");
  let fetched = false;
  const result = await withPageGlobals(
    { pathname: "/en/somewhere-else", fetchImpl: async () => { fetched = true; }, nextData: { buildId: "b" } },
    () => func("/en/@Terminalfoo/collections"),
  );
  assert.equal(result.navigated, true);
  assert.equal(fetched, false);
});

test("in-page read: fetches the data route with a 15s abort signal and returns only slimmed props", async () => {
  const func = await captureInPageFunc("readCollectionsDataInPage");
  let seenInit;
  const result = await withPageGlobals(
    {
      pathname: "/en/@Terminalfoo/collections",
      nextData: { buildId: "b", props: { pageProps: { favoritesList: [], huge: "x".repeat(10000) } } },
      fetchImpl: async (url, init) => {
        seenInit = init;
        return {
          ok: true,
          json: async () => ({ pageProps: { favoritesList: [{ id: 1 }], bigString: "y".repeat(5000), info: { id: 1, title: "t", deep: { a: 1 } } } }),
        };
      },
    },
    () => func("/en/@Terminalfoo/collections"),
  );
  assert.ok(seenInit.signal instanceof AbortSignal);
  assert.deepEqual(Object.keys(result.routePageProps).sort(), ["favoritesList", "info"]);
  assert.deepEqual(result.routePageProps.info, { id: 1, title: "t" }); // nested objects dropped
  assert.equal("huge" in result.nextData.props.pageProps, false);
});

test("in-page data-route fetch: aborts when the tab navigated away and carries an abort signal", async () => {
  // Drive a sync once so the spy can capture the injected function.
  const { exec, calls } = routedExec({
    page: listPage([LIST[0]]),
    routes: { "/en/collections/1-one": route({ designs: [design(1)] }) },
  });
  let captured;
  const spy = async (tabId, f, args) => {
    if (f.name === "fetchCollectionDataRouteInPage") captured = f;
    return exec(tabId, f, args);
  };
  const { api } = stubApi();
  await syncCollections({ tabId: 1, url: LIST_URL, exec: spy, api, report: () => {} });
  let seenInit;
  const moved = await withPageGlobals({ pathname: "/other", fetchImpl: async () => { throw new Error("no"); } }, () =>
    captured("b", "/en/collections/1-one", "/en/@Terminalfoo/collections"),
  );
  assert.equal(moved, null);
  const okResult = await withPageGlobals(
    {
      pathname: "/en/@Terminalfoo/collections",
      fetchImpl: async (u, init) => {
        seenInit = init;
        return { ok: true, json: async () => ({ pageProps: { designs: [{ id: 1 }], noise: { a: { b: 1 } }, s: "str" } }) };
      },
    },
    () => captured("b", "/en/collections/1-one", "/en/@Terminalfoo/collections"),
  );
  assert.ok(seenInit.signal instanceof AbortSignal);
  assert.deepEqual(okResult, { pageProps: { designs: [{ id: 1 }], noise: {} } });
});

// ---------------------------------------------------------------------------
// readCollectionDetailPage / syncCollectionDetail
// ---------------------------------------------------------------------------

const INFO = { id: 18925823, title: "ESP32", designCnt: 1 };

function detailExec(pageResult) {
  return routedExec({ page: pageResult }).exec;
}

test("readCollectionDetailPage: stale inline data is ignored unless its collection info id matches the URL", async () => {
  const stale = {
    nextData: { buildId: "b", props: { pageProps: { designs: [design(1)], favoritesInfo: { id: 999, title: "Other", designCnt: 1 } } } },
    routePageProps: null,
  };
  const page = await readCollectionDetailPage({ tabId: 1, url: DETAIL_URL, exec: detailExec(stale) });
  assert.equal(page.found, false);

  const fresh = {
    nextData: { buildId: "b", props: { pageProps: { designs: [design(1)], favoritesInfo: INFO } } },
    routePageProps: null,
  };
  const page2 = await readCollectionDetailPage({ tabId: 1, url: DETAIL_URL, exec: detailExec(fresh) });
  assert.equal(page2.found, true);
  assert.equal(page2.title, "ESP32");
  assert.equal(page2.key, "designs");
});

test("readCollectionDetailPage: null when the tab navigated away mid-read", async () => {
  const page = await readCollectionDetailPage({
    tabId: 1,
    url: DETAIL_URL,
    exec: detailExec({ navigated: true, nextData: null, routePageProps: null }),
  });
  assert.equal(page, null);
});

function detailPage(pageProps) {
  return { nextData: null, routePageProps: pageProps };
}

test("syncCollectionDetail: upserts ONE entry via upsertCollection (never the full-replace pushCollections), then pushes items", async () => {
  const exec = detailExec(detailPage({ favoritesInfo: INFO, designs: [design(1)] }));
  const { api, calls } = stubApi();
  const { report, calls: reports } = reporter();
  const result = await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api, report });
  assert.equal(calls.pushCollections.length, 0);
  assert.deepEqual(calls.upsertCollection, [
    { site: "makerworld", entry: { list_id: "18925823", title: "ESP32", slug: "esp32", count: 1, is_default: false } },
  ]);
  assert.equal(calls.pushCollectionItems.length, 1);
  assert.deepEqual(result, { listId: "18925823", title: "ESP32", items: 1, partial: false, countDerived: true, persistable: true });
  assert.equal(reports.at(-1).text, 'Synced "ESP32": 1 item.');
});

test("syncCollectionDetail: a truncated read (raw length < count) pushes nothing and reports a WARNING, not ok", async () => {
  const exec = detailExec(detailPage({ favoritesInfo: { ...INFO, designCnt: 5 }, designs: [design(1), design(2)] }));
  const { api, calls } = stubApi();
  const { report, calls: reports } = reporter();
  const result = await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api, report });
  assert.equal(result.partial, true);
  assert.equal(result.persistable, false);
  assert.equal(calls.upsertCollection.length + calls.pushCollectionItems.length, 0);
  assert.equal(reports.at(-1).kind, "warn");
  assert.match(reports.at(-1).text, /Partial read \(2 of 5\)/);
});

test("syncCollectionDetail: partial check uses the RAW length, so malformed dropped entries don't make it partial", async () => {
  const exec = detailExec(
    detailPage({ favoritesInfo: { ...INFO, designCnt: 4 }, designs: [design(1), design(2), design(3), { id: 4 }] }),
  );
  const { api, calls } = stubApi();
  const result = await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api, report: () => {} });
  assert.equal(result.partial, false);
  assert.equal(calls.pushCollectionItems[0].items.length, 3);
});

test("syncCollectionDetail: count not derivable + unnamed list key -> nothing pushed", async () => {
  const exec = detailExec(detailPage({ favoritesInfo: { id: 18925823, title: "ESP32" }, list: [design(1)] }));
  const { api, calls } = stubApi();
  const { report, calls: reports } = reporter();
  const result = await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api, report });
  assert.equal(result.partial, true);
  assert.equal(result.persistable, false);
  assert.equal(calls.upsertCollection.length + calls.pushCollectionItems.length, 0);
  assert.equal(reports.at(-1).kind, "warn");
});

test("syncCollectionDetail: count not derivable + named key -> pushes with count null and is persistable", async () => {
  const exec = detailExec(detailPage({ favoritesInfo: { id: 18925823, title: "ESP32" }, designs: [design(1), design(2)] }));
  const { api, calls } = stubApi();
  const result = await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api, report: () => {} });
  assert.equal(calls.upsertCollection[0].entry.count, null);
  assert.equal(calls.pushCollectionItems[0].items.length, 2);
  assert.equal(result.countDerived, false);
  assert.equal(result.persistable, true);
});

test("syncCollectionDetail: trusted-empty with count 0 pushes []; empty with unknown count pushes nothing", async () => {
  const exec = detailExec(detailPage({ favoritesInfo: { ...INFO, designCnt: 0 }, designs: [] }));
  const { api, calls } = stubApi();
  const result = await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api, report: () => {} });
  assert.deepEqual(calls.pushCollectionItems, [{ site: "makerworld", listId: "18925823", items: [] }]);
  assert.equal(result.persistable, true);

  const exec2 = detailExec(detailPage({ favoritesInfo: { id: 18925823, title: "ESP32" }, designs: [] }));
  const { api: api2, calls: calls2 } = stubApi();
  await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec: exec2, api: api2, report: () => {} });
  assert.equal(calls2.pushCollectionItems.length, 0);
});

test("syncCollectionDetail: no derivable title -> items only (no upsert)", async () => {
  const exec = detailExec(detailPage({ designs: [design(1)] }));
  const { api, calls } = stubApi();
  const { report, calls: reports } = reporter();
  await syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api, report });
  assert.equal(calls.upsertCollection.length, 0);
  assert.equal(calls.pushCollectionItems.length, 1);
  assert.equal(reports.at(-1).text, 'Synced "18925823": 1 item.');
});

test("syncCollectionDetail: rejects for a non-detail URL, unreadable items, and a rejected upsert/items push", async () => {
  const { api } = stubApi();
  await assert.rejects(
    syncCollectionDetail({ tabId: 1, url: "https://makerworld.com/en/models/1", exec: detailExec({}), api, report: () => {} }),
    /isn't a collection page/,
  );
  await assert.rejects(
    syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec: detailExec(detailPage({ nothing: 1 })), api, report: () => {} }),
    /Couldn't read this collection/,
  );
  const exec = detailExec(detailPage({ favoritesInfo: INFO, designs: [design(1)] }));
  const { api: badUpsert, calls } = stubApi({ upsertCollection: async () => err("upsert rejected") });
  await assert.rejects(syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api: badUpsert, report: () => {} }), /upsert rejected/);
  assert.equal(calls.pushCollectionItems.length, 0);
  const { api: badItems } = stubApi({ pushCollectionItems: async () => err("items rejected") });
  await assert.rejects(syncCollectionDetail({ tabId: 1, url: DETAIL_URL, exec, api: badItems, report: () => {} }), /items rejected/);
});

test("syncCollectionDetail: an injected page skips the read; no slug in URL gives null slug", async () => {
  const exec = async () => {
    throw new Error("should not read");
  };
  const { api, calls } = stubApi();
  const page = {
    parsed: { id: "18925823", slug: null },
    items: [{ external_id: "1", title: "A", url: "u", author: null, thumbnail_url: null }],
    rawLength: 1,
    title: "ESP32",
    count: 1,
    found: true,
    key: "designs",
  };
  await syncCollectionDetail({ tabId: 1, url: "https://makerworld.com/collections/18925823", exec, api, report: () => {}, page });
  assert.equal(calls.upsertCollection[0].entry.slug, null);
});
