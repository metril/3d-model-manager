import { test } from "node:test";
import assert from "node:assert/strict";

import { getState, setConfig } from "../src/config.js";
import { install } from "./chromeStub.js";

const DETAIL_URL = "https://makerworld.com/en/collections/18925823-esp32";

let pageResult = null;
const stub = install({
  scripting: { executeScript: async () => [{ result: pageResult }] },
  cookies: {
    getAllCookieStores: async () => [{ id: "0" }],
    get: async () => ({ value: "cookie-1", domain: "makerworld.com" }),
    getAll: async () => [],
    onChanged: { addListener: (fn) => (stub.listeners["cookies.onChanged"] ||= []).push(fn) },
  },
});

const fetchCalls = [];
globalThis.fetch = async (url, init) => {
  fetchCalls.push({ url, method: init?.method, body: init?.body });
  return { ok: true, status: 200, statusText: "OK", json: async () => ({ ok: true }) };
};

const bg = await import("../src/background.js");
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const call = (name) => stub.calls.filter((c) => c[0] === name);

test("worker start: the courier alarm is ensured and the stale badge cleared at top level", async () => {
  await tick();
  assert.equal(call("alarms.create").length, 1);
  assert.equal(call("alarms.create")[0][1], "makerworld-courier");
  assert.deepEqual(call("action.setBadgeText")[0][1], { text: "" });
});

test("all event listeners are registered at top level", () => {
  for (const name of [
    "runtime.onInstalled",
    "runtime.onStartup",
    "runtime.onMessage",
    "contextMenus.onClicked",
    "tabs.onUpdated",
    "webNavigation.onHistoryStateUpdated",
    "cookies.onChanged",
    "alarms.onAlarm",
  ]) {
    assert.ok((stub.listeners[name] || []).length >= 1, `${name} listener missing`);
  }
});

test("cookies.onChanged for the makerworld `token` cookie triggers the courier; other cookies don't", async () => {
  await setConfig({ appBaseUrl: "https://app.test", apiToken: "tok" });
  const [listener] = stub.listeners["cookies.onChanged"];
  listener({ cookie: { name: "other", domain: ".makerworld.com" }, removed: false });
  listener({ cookie: { name: "token", domain: ".example.com" }, removed: false });
  listener({ cookie: { name: "token", domain: ".makerworld.com" }, removed: true });
  await tick();
  assert.equal(fetchCalls.length, 0);
  listener({ cookie: { name: "token", domain: ".makerworld.com" }, removed: false });
  await tick(80);
  assert.equal(fetchCalls.length, 1);
  assert.match(fetchCalls[0].url, /\/api\/ext\/credentials\/makerworld$/);
  assert.equal((await getState()).lastCourierStatus, "Cookie sent to the app.");
});

test("the alarm routes through the same single-flight courier", async () => {
  const [onAlarm] = stub.listeners["alarms.onAlarm"];
  const before = fetchCalls.length;
  onAlarm({ name: "makerworld-courier" });
  onAlarm({ name: "something-else" });
  await tick(80);
  assert.equal(fetchCalls.length, before); // hash unchanged -> nothing pushed
  assert.equal((await getState()).lastCourierStatus, "Cookie up to date.");
});

test("messages: a foreign sender (content-script/web page) is ignored; our own page can save", async () => {
  const [onMessage] = stub.listeners["runtime.onMessage"];
  let responded = false;
  assert.equal(
    onMessage({ type: "save", url: "https://makerworld.com/en/models/1" }, { id: "test-ext", url: "https://evil.example/" }, () => (responded = true)),
    false,
  );
  assert.equal(responded, false);
  const answer = new Promise((resolve) =>
    assert.equal(
      onMessage(
        { type: "save", url: "https://makerworld.com/en/models/1" },
        { id: "test-ext", url: "chrome-extension://test-ext/src/popup.html" },
        resolve,
      ),
      true,
    ),
  );
  const response = await answer;
  assert.equal(response.ok, true);
});

test("context menu: a non-model URL flashes a per-tab error badge and never throws", async () => {
  const [onClicked] = stub.listeners["contextMenus.onClicked"];
  const before = call("action.setBadgeText").length;
  await onClicked({ menuItemId: "save-to-my-library-link", linkUrl: "https://makerworld.com/en/search" }, { id: 7 });
  const texts = call("action.setBadgeText").slice(before);
  assert.deepEqual(texts[0][1], { text: "!", tabId: 7 });
  // a throwing API is swallowed
  stub.chrome.action.setBadgeText = async () => {
    throw new Error("no such tab");
  };
  await onClicked({ menuItemId: "save-to-my-library-link", linkUrl: "https://makerworld.com/en/search" }, { id: 7 });
});

function detailProps({ designs, designCnt }) {
  return { nextData: null, routePageProps: { favoritesInfo: { id: 18925823, title: "ESP32", designCnt }, designs } };
}

test("detail auto-sync: upserts one entry (PUT) and persists the throttle hash only for a complete read", async () => {
  await setConfig({ appBaseUrl: "https://app.test", apiToken: "tok" });
  fetchCalls.length = 0;
  pageResult = detailProps({ designs: [{ id: 1, title: "A" }, { id: 2, title: "B" }], designCnt: 2 });
  await bg.runCollectionDetailSync(1, DETAIL_URL);
  assert.deepEqual(
    fetchCalls.map((c) => c.method),
    ["PUT", "POST"],
  );
  assert.match(fetchCalls[0].url, /\/api\/ext\/collections\/18925823$/);
  assert.equal((await getState()).lastCollectionItemsHash.length, 1);

  // unchanged -> throttled, no further calls
  fetchCalls.length = 0;
  await bg.runCollectionDetailSync(1, DETAIL_URL);
  assert.equal(fetchCalls.length, 0);
});

test("detail auto-sync: a truncated read pushes nothing and leaves the hash alone", async () => {
  fetchCalls.length = 0;
  pageResult = detailProps({ designs: [{ id: 1, title: "A" }, { id: 3, title: "C" }], designCnt: 9 });
  await bg.runCollectionDetailSync(1, DETAIL_URL);
  assert.equal(fetchCalls.length, 0);
  assert.equal((await getState()).lastCollectionItemsHash.length, 1); // still just the earlier one
});

test("detail auto-sync: a confirmed-empty collection (count 0) pushes [] and persists", async () => {
  fetchCalls.length = 0;
  pageResult = detailProps({ designs: [], designCnt: 0 });
  await bg.runCollectionDetailSync(1, DETAIL_URL);
  const itemsPush = fetchCalls.find((c) => c.method === "POST");
  assert.deepEqual(JSON.parse(itemsPush.body).items, []);
  assert.equal((await getState()).lastCollectionItemsHash.length, 1); // same listId upserted, not duplicated
});

test("detail auto-sync: an empty read with an unknown count is not pushed", async () => {
  fetchCalls.length = 0;
  pageResult = { nextData: null, routePageProps: { favoritesInfo: { id: 18925823, title: "ESP32" }, designs: [] } };
  await bg.runCollectionDetailSync(1, DETAIL_URL);
  assert.equal(fetchCalls.length, 0);
});

test("a collections-list run with a rejected push never advances the list hash", async () => {
  fetchCalls.length = 0;
  const entries = [{ id: 1, title: "One", slug: "one", designCnt: 1 }];
  pageResult = { nextData: { buildId: "b", props: { pageProps: { favoritesList: entries } } }, routePageProps: { favoritesList: entries } };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 500, statusText: "x", json: async () => ({ detail: "down" }) });
  try {
    await bg.runCollectionsSync(1, "https://makerworld.com/en/@me/collections");
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal((await getState()).lastCollectionsHash, null);
});

test("context menu: 'Save & queue' entries exist only while a printer key is set (rebuilt on change)", async () => {
  const since = async (fn) => {
    const before = call("contextMenus.create").length;
    await fn();
    await tick(40);
    return call("contextMenus.create").slice(before).map((c) => c[1].title);
  };
  await setConfig({ printerKey: "" });
  assert.deepEqual(await since(() => bg.ensureContextMenu()), ["Save model to my library", "Save model to my library"]);
  // storage.onChanged rebuilds the menu when the key appears...
  const withKey = await since(() => setConfig({ printerKey: "pk" }));
  assert.equal(withKey.filter((t) => t === "Save & queue to printer").length, 2);
  // ...and again when it is cleared.
  const cleared = await since(() => setConfig({ printerKey: "" }));
  assert.equal(cleared.filter((t) => t === "Save & queue to printer").length, 0);
  assert.ok(cleared.length >= 2);
});

test("messages: a 'queue' message without a printer key is refused, not silently dropped", async () => {
  const [onMessage] = stub.listeners["runtime.onMessage"];
  const answer = new Promise((resolve) =>
    onMessage({ type: "queue", importId: 1 }, { id: "test-ext", url: "chrome-extension://test-ext/src/popup.html" }, resolve),
  );
  const response = await answer;
  assert.equal(response.ok, false);
  assert.match(response.error, /printer key/);
});
