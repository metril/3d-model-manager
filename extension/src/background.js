/**
 * MV3 service worker. Wires together the pure modules (`detect.js`,
 * `courier.js`, `api.js`, `config.js`, `syncFlow.js`) with the `chrome.*`
 * APIs. Nothing in here is unit-tested directly (it needs a live extension
 * context); the logic it delegates to (site/model-page/collections-page
 * detection, the cookie-changed diff, the collections sync flow itself) IS
 * covered by `test/detect.test.js`, `test/courier.test.js`, and
 * `test/syncFlow.test.js`.
 */

import { GALLERY_HOST_PATTERNS, isCollectionDetailPage, isCollectionsPage, isModelPage } from "./detect.js";
import { createClient } from "./api.js";
import { getConfig, isConfigured, setConfig } from "./config.js";
import { hashToken, pickCookieValue, shouldPush } from "./courier.js";
import {
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
} from "./syncFlow.js";

const CONTEXT_MENU_PAGE_ID = "save-to-my-library-page";
const CONTEXT_MENU_LINK_ID = "save-to-my-library-link";
const COURIER_ALARM_NAME = "makerworld-courier";
const COURIER_ALARM_PERIOD_MINUTES = 30;
const MAKERWORLD_COOKIE_DOMAIN = "makerworld.com";
const MAKERWORLD_COOKIE_HOSTS = new Set(["makerworld.com", "www.makerworld.com"]);
const MAKERWORLD_COOKIE_NAME = "token";
const BADGE_FLASH_MS = 3000;
const BADGE_OK_COLOR = "#2e7d32";
const BADGE_ERROR_COLOR = "#b3261e";

function ensureContextMenu() {
  // Two separate items, not one item with both contexts: Chrome ANDs
  // `documentUrlPatterns` and `targetUrlPatterns` together on a single
  // item, so a combined item would require the CURRENT PAGE to also be a
  // gallery host before a matching LINK's menu entry could ever show —
  // which would hide the entry for the common case of right-clicking a
  // gallery link from an unrelated page (a forum post, a search result,
  // etc). Splitting keeps each restriction independent.
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: CONTEXT_MENU_PAGE_ID,
      title: "Save model to my library",
      contexts: ["page"],
      documentUrlPatterns: GALLERY_HOST_PATTERNS,
    });
    chrome.contextMenus.create({
      id: CONTEXT_MENU_LINK_ID,
      title: "Save model to my library",
      contexts: ["link"],
      targetUrlPatterns: GALLERY_HOST_PATTERNS,
    });
  });
}

// `alarms.create` with an existing name resets its period, so only create
// when missing (otherwise every browser start would postpone the next tick).
async function ensureCourierAlarm() {
  const existing = await chrome.alarms.get(COURIER_ALARM_NAME);
  if (!existing || existing.periodInMinutes !== COURIER_ALARM_PERIOD_MINUTES) {
    chrome.alarms.create(COURIER_ALARM_NAME, { periodInMinutes: COURIER_ALARM_PERIOD_MINUTES });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  ensureContextMenu();
  ensureCourierAlarm().catch((err) => console.warn("courier alarm setup failed", err));
});

// Alarms persist across browser restarts, but re-asserting on startup is
// cheap and guards against the alarm having been cleared some other way.
chrome.runtime.onStartup.addListener(() => {
  ensureCourierAlarm().catch((err) => console.warn("courier alarm setup failed", err));
});

/**
 * Shared "save this URL" flow used by both the popup's message and the
 * context-menu click. Never throws — always resolves to either
 * `{needsConfig:true}` or the normalized `api.js` result.
 * @param {string} url
 */
async function saveUrl(url) {
  const config = await getConfig();
  if (!isConfigured(config)) {
    return { needsConfig: true };
  }
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  return client.createImport(url);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender?.id !== chrome.runtime.id) {
    return false; // only our own extension pages may trigger a save
  }
  if (message && message.type === "save" && typeof message.url === "string") {
    saveUrl(message.url)
      .then(sendResponse)
      .catch((err) => sendResponse({ ok: false, error: err?.message || "Save failed" }));
    return true; // keep the message channel open for the async response
  }
  return false;
});

async function flashBadge(text, color) {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
  setTimeout(() => {
    chrome.action.setBadgeText({ text: "" });
  }, BADGE_FLASH_MS);
}

// We didn't request the `notifications` permission (see manifest), so
// context-menu feedback is a brief action-badge flash instead of a toast.
chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== CONTEXT_MENU_PAGE_ID && info.menuItemId !== CONTEXT_MENU_LINK_ID) {
    return;
  }
  const url = info.linkUrl || info.pageUrl;
  if (!url || !isModelPage(url)) {
    await flashBadge("!", BADGE_ERROR_COLOR);
    return;
  }
  const result = await saveUrl(url);
  if (result && result.ok) {
    await flashBadge("✓", BADGE_OK_COLOR);
  } else if (result && result.needsConfig) {
    // Not just a badge flash: an unexplained "!" doesn't tell the user
    // *why* the save failed, so send them straight to setup.
    await chrome.runtime.openOptionsPage();
  } else {
    await flashBadge("!", BADGE_ERROR_COLOR);
  }
});

/**
 * Reads the MakerWorld `token` cookie and, if it's present and different
 * from the last successfully-pushed value (compared by hash — the raw
 * cookie is never persisted), couriers it to the app and records the new
 * hash on success.
 */
async function runCourier() {
  const config = await getConfig();
  if (!config.autoCourier || !isConfigured(config)) {
    return;
  }
  const cookies = await chrome.cookies.getAll({
    domain: MAKERWORLD_COOKIE_DOMAIN,
    name: MAKERWORLD_COOKIE_NAME,
  });
  const value = pickCookieValue(cookies);
  if (!value) {
    return;
  }
  const needsPush = await shouldPush(value, config.lastMakerworldHash);
  if (!needsPush) {
    return;
  }
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  const result = await client.setMakerworldCredential(value);
  if (result.ok) {
    const newHash = await hashToken(value);
    await setConfig({ lastMakerworldHash: newHash });
  }
}

/**
 * `exec(tabId, func, args)` seam `syncFlow.js` needs -- the one place this
 * file wraps `chrome.scripting.executeScript` for the collections flow
 * (mirrors `popup.js`'s identically-named helper; both wrap the same
 * `chrome.*` call, but each file owns its own thin copy rather than adding
 * a shared module just for this).
 */
async function execInTab(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return results && results[0] && results[0].result;
}

/**
 * Auto-sync entry point (import-health branch T5): runs the SAME shared
 * flow the popup's "Sync collections to app" button uses (`syncFlow.js`),
 * triggered by simply VISITING the MakerWorld collections page instead of
 * requiring a manual click. Mirrors `runCourier`'s shape -- gated on its
 * own setting (`autoSyncCollections`), and since there's no popup open to
 * show an error to, failures are logged to `console.*` only and NEVER
 * thrown/surfaced to the tab.
 *
 * Throttled like the cookie courier (`shouldPush`/`lastMakerworldHash`),
 * but on the pushed collections payload instead of the cookie
 * (`shouldPushCollections`/`lastCollectionsHash`, `syncFlow.js`). The
 * comparison happens BEFORE any push (list or items) -- an unchanged page
 * costs nothing but the one in-page read that produced `entries`. Note:
 * `entries` carries each collection's `count` (MakerWorld's `designCnt`),
 * so adding/removing an item from a named collection necessarily changes
 * that collection's `count` and therefore the hash -- membership changes
 * are covered by hashing the list alone, without needing to hash the
 * fetched item ids too. (Not independently re-verified live for this task
 * -- `designCnt` being a literal per-collection item count makes this the
 * only sane reading, and M9's capture notes found no way to probe MakerWorld
 * from a server IP to double-check; if a future capture ever shows
 * `designCnt` staying put across a real membership edit, this reasoning --
 * and the throttle -- needs revisiting.)
 *
 * An empty `entries` read is treated as "nothing to sync" and never pushed
 * automatically -- MakerWorld's own scrape can come back transiently empty
 * from a real browser same as it does from the server (see the README's
 * "empty isn't proof of empty" caution for the courier), and blowing away
 * real cached collections on a flaky read would be worse than doing nothing
 * until the next successful visit.
 *
 * The same caution applies one level down: a run where the LIST read fine
 * but one or more collections' ITEMS came back unreadable
 * (`result.unreadable`, `syncFlow.js`) still pushed what it could, but must
 * NOT advance `lastCollectionsHash` (`shouldPersistHash`) -- otherwise the
 * throttle would treat that partial run as done and never retry the
 * unreadable collections until the list itself changes.
 */
async function runCollectionsSync(tabId, url) {
  const config = await getConfig();
  if (!config.autoSyncCollections || !isConfigured(config)) {
    return;
  }

  const page = await readCollectionsPage({ tabId, exec: execInTab });
  if (!page) {
    console.error("[collections auto-sync] couldn't read the collections page");
    return;
  }
  if (page.entries.length === 0) {
    return;
  }

  const needsPush = await shouldPushCollections(page.entries, config.lastCollectionsHash);
  if (!needsPush) {
    return;
  }

  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  let result;
  try {
    result = await syncCollections({
      tabId,
      url,
      exec: execInTab,
      api: client,
      page,
      report: (text, kind) => {
        const line = `[collections auto-sync] ${text}`;
        if (kind === "error") {
          console.error(line);
        } else {
          console.log(line);
        }
      },
    });
  } catch {
    // `syncCollections` already logged the failure reason above via
    // `report`. Leave `lastCollectionsHash` untouched so the next visit
    // retries instead of silently giving up forever.
    return;
  }

  if (!shouldPersistHash(result)) {
    // Partial success -- some collections' items were unreadable. Leave
    // `lastCollectionsHash` untouched (same reasoning as the catch above)
    // so the next visit retries them.
    return;
  }

  const newHash = await hashCollectionsPayload(page.entries);
  await setConfig({ lastCollectionsHash: newHash });
}

/**
 * Auto-sync entry point for a collection DETAIL page (M11) -- mirrors
 * `runCollectionsSync` above (same setting, same silent-to-the-user/
 * logged-only failure handling), but drives `syncCollectionDetail`
 * (`syncFlow.js`) instead: the guaranteed-correct single-collection sync,
 * triggered by simply VISITING that one collection's own page.
 *
 * Throttled per-collection via `lastCollectionItemsHash`
 * (`config.js`/`syncFlow.js`'s `findLastCollectionItemsHash`/
 * `upsertCollectionItemsHash`) rather than the whole-payload
 * `lastCollectionsHash` the bulk flow uses -- a hash of just THIS
 * collection's item ids, so visiting one collection's page repeatedly
 * doesn't re-push it every time, but visiting a DIFFERENT collection's page
 * (or this one after its membership actually changed) still does. An empty
 * `items` read is treated as "nothing to sync" and never pushed, same
 * "empty isn't proof of empty" caution as `runCollectionsSync`.
 *
 * I1 hardening: `syncCollectionDetail` reads this collection's items off ONE
 * un-paged SSR response, with no paged fallback to retry a short read with
 * -- pushing a truncated read would silently shrink the backend's cached
 * membership (`pushCollectionItems` is a replace-set). It signals that back
 * via `partial`/`countDerived` on its resolved result (see its own doc); the
 * throttle hash below is only persisted for a CONFIRMED-COMPLETE read
 * (`!partial && countDerived`) -- persisting it for a truncated or
 * uncertain-completeness read would make the throttle think this collection
 * is done syncing and stop retrying it.
 */
async function runCollectionDetailSync(tabId, url) {
  const config = await getConfig();
  if (!config.autoSyncCollections || !isConfigured(config)) {
    return;
  }

  const page = await readCollectionDetailPage({ tabId, url, exec: execInTab });
  if (!page || !page.found) {
    console.error("[collection detail auto-sync] couldn't read this collection");
    return;
  }
  if (page.items.length === 0) {
    return;
  }

  const listId = page.parsed.id;
  const lastHashes = config.lastCollectionItemsHash || [];
  const itemsHash = await hashCollectionItemsPayload(page.items);
  if (findLastCollectionItemsHash(lastHashes, listId) === itemsHash) {
    return;
  }

  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  let result;
  try {
    result = await syncCollectionDetail({
      tabId,
      url,
      exec: execInTab,
      api: client,
      page,
      report: (text, kind) => {
        const line = `[collection detail auto-sync] ${text}`;
        if (kind === "error") {
          console.error(line);
        } else {
          console.log(line);
        }
      },
    });
  } catch {
    // `syncCollectionDetail` already logged the failure reason above via
    // `report`. Leave the throttle hash untouched so the next visit retries.
    return;
  }

  if (result.partial || !result.countDerived) {
    // A truncated read (nothing was pushed) or a read whose completeness
    // couldn't even be confirmed (pushed, but no count to check it against)
    // -- either way, leave the throttle hash untouched so the next visit
    // retries instead of treating this run as done (I1).
    return;
  }

  await setConfig({ lastCollectionItemsHash: upsertCollectionItemsHash(lastHashes, listId, itemsHash) });
}

// `status:"complete"` can fire repeatedly for one tab (SPA navigations,
// subframes); skip a trigger while the same tab+type is still running.
const inFlight = new Set();
function runOnce(key, fn) {
  if (inFlight.has(key)) {
    return Promise.resolve();
  }
  inFlight.add(key);
  return Promise.resolve()
    .then(fn)
    .finally(() => inFlight.delete(key));
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") {
    return;
  }
  const url = tab && tab.url;
  if (!url) {
    return;
  }
  let hostname;
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    return;
  }
  if (MAKERWORLD_COOKIE_HOSTS.has(hostname)) {
    runOnce("courier", runCourier).catch((err) => console.warn("courier failed", err));
  }
  if (tab.id !== undefined && isCollectionsPage(url)) {
    // `runCollectionsSync` isn't awaited here (this listener can't be
    // async-blocking), so an unhandled rejection anywhere in its chain --
    // `getConfig`/`isConfigured`/`shouldPushCollections` sit outside its own
    // try/catch -- would otherwise surface as an unhandled promise
    // rejection instead of the silent-to-the-user, logged-only failure this
    // background sync is meant to be.
    runOnce(`${tabId}:collections`, () => runCollectionsSync(tab.id, url)).catch((err) =>
      console.warn("collections auto-sync failed", err),
    );
  }
  if (tab.id !== undefined && isCollectionDetailPage(url)) {
    // Same not-awaited/`.catch`-guarded shape as `runCollectionsSync` above,
    // for the same reason.
    runOnce(`${tabId}:collection-detail`, () => runCollectionDetailSync(tab.id, url)).catch((err) =>
      console.warn("collection detail auto-sync failed", err),
    );
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === COURIER_ALARM_NAME) {
    runCourier().catch((err) => console.warn("courier failed", err));
  }
});
