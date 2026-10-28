/**
 * Extension background script (MV3 service worker on Chrome, event page on
 * Firefox). Wires the pure modules (`detect.js`, `courierRun.js`, `api.js`,
 * `config.js`, `syncFlow.js`) to the `chrome.*` events. Every listener is
 * registered synchronously at top level (required for MV3 wake-ups).
 *
 * Triggers:
 *   - cookie courier: `cookies.onChanged` (primary), a 30-minute alarm
 *     (backstop), and any makerworld.com tab load;
 *   - collections auto-sync: `tabs.onUpdated` (full loads) and
 *     `webNavigation.onHistoryStateUpdated` (SPA navigations);
 *   - save: popup message / context menu.
 * Background failures are silent to the user (logged only).
 */

import { MAKERWORLD_HOSTS } from "./collections.js";
import { createClient } from "./api.js";
import { getConfig, getState, isConfigured, setState } from "./config.js";
import { runCourier } from "./courierRun.js";
import { GALLERY_HOST_PATTERNS, isCollectionDetailPage, isCollectionsPage, isModelPage } from "./detect.js";
import { execInTab } from "./exec.js";
import { isOwnExtensionSender } from "./guards.js";
import { runOnce } from "./runOnce.js";
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
const BADGE_OK_COLOR = "#2e7d32";
const BADGE_ERROR_COLOR = "#b3261e";

function ensureContextMenu() {
  // Two separate items, not one with both contexts: Chrome ANDs
  // `documentUrlPatterns` and `targetUrlPatterns` on a single item, which
  // would hide the link entry on non-gallery pages.
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
// when missing (otherwise every start would postpone the next tick).
export async function ensureCourierAlarm() {
  const existing = await chrome.alarms.get(COURIER_ALARM_NAME);
  if (!existing || existing.periodInMinutes !== COURIER_ALARM_PERIOD_MINUTES) {
    chrome.alarms.create(COURIER_ALARM_NAME, { periodInMinutes: COURIER_ALARM_PERIOD_MINUTES });
  }
}

/** Runs on EVERY worker start (not only install/startup events). */
function onWorkerStart() {
  ensureCourierAlarm().catch((err) => console.warn("courier alarm setup failed", err));
  // Clear any stale global badge left by a previous run.
  Promise.resolve(chrome.action.setBadgeText({ text: "" })).catch(() => {});
}
onWorkerStart();

chrome.runtime.onInstalled.addListener(() => {
  ensureContextMenu();
  onWorkerStart();
});
chrome.runtime.onStartup.addListener(onWorkerStart);

/**
 * Shared "save this URL" flow (popup message and context menu). Never throws.
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
  if (!isOwnExtensionSender(sender, chrome.runtime)) {
    return false; // only our own extension pages may trigger a save
  }
  if (message && message.type === "save" && typeof message.url === "string") {
    saveUrl(message.url)
      .then(sendResponse)
      .catch((err) => sendResponse({ ok: false, error: err?.message || "Save failed" }));
    return true; // keep the channel open for the async response
  }
  return false;
});

/**
 * Per-tab action badge. A tab-scoped badge disappears when that tab
 * navigates (and is cleared on worker start / popup open), so no timer is
 * needed to clear it.
 */
async function flashBadge(text, color, tabId) {
  const scope = tabId === undefined ? {} : { tabId };
  await chrome.action.setBadgeBackgroundColor({ color, ...scope });
  await chrome.action.setBadgeText({ text, ...scope });
}

// No `notifications` permission, so context-menu feedback is a badge.
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const tabId = tab && tab.id;
  try {
    if (info.menuItemId !== CONTEXT_MENU_PAGE_ID && info.menuItemId !== CONTEXT_MENU_LINK_ID) {
      return;
    }
    const url = info.linkUrl || info.pageUrl;
    if (!url || !isModelPage(url)) {
      await flashBadge("!", BADGE_ERROR_COLOR, tabId);
      return;
    }
    const result = await saveUrl(url);
    if (result && result.ok) {
      await flashBadge("✓", BADGE_OK_COLOR, tabId);
    } else if (result && result.needsConfig) {
      await chrome.runtime.openOptionsPage();
    } else {
      await flashBadge("!", BADGE_ERROR_COLOR, tabId);
    }
  } catch (err) {
    console.warn("context-menu save failed", err);
    await flashBadge("!", BADGE_ERROR_COLOR, tabId).catch(() => {});
  }
});

const logReport = (prefix) => (text, kind) => {
  const line = `[${prefix}] ${text}`;
  if (kind === "error") {
    console.error(line);
  } else if (kind === "warn") {
    console.warn(line);
  } else {
    console.log(line);
  }
};

/**
 * Collections LIST auto-sync: the same shared flow the popup button uses,
 * triggered by visiting the page. Throttled on a hash of the pushed payload
 * BEFORE any push. An empty read is never pushed (a flaky empty scrape must
 * not blow away real cached collections). A run with unreadable, failed or
 * partial collections must NOT advance the hash, so the next visit retries.
 */
export async function runCollectionsSync(tabId, url) {
  const config = await getConfig();
  if (!config.autoSyncCollections || !isConfigured(config)) {
    return;
  }

  const page = await readCollectionsPage({ tabId, url, exec: execInTab });
  if (!page) {
    console.error("[collections auto-sync] couldn't read the collections page");
    return;
  }
  if (page.entries.length === 0) {
    return;
  }

  const state = await getState();
  if (!(await shouldPushCollections(page.entries, state.lastCollectionsHash))) {
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
      report: logReport("collections auto-sync"),
    });
  } catch {
    return; // already reported; leave the hash so the next visit retries
  }

  if (!shouldPersistHash(result)) {
    return;
  }
  const newHash = await hashCollectionsPayload(page.entries);
  await setState({ lastCollectionsHash: newHash });
}

/**
 * Collection DETAIL auto-sync, throttled per collection via
 * `lastCollectionItemsHash`. An empty read is only acted on when it is a
 * confirmed-empty collection (trusted key AND derived count 0), so removals
 * propagate; otherwise nothing is pushed. The throttle hash is persisted only
 * when `syncCollectionDetail` says the read is `persistable`.
 */
export async function runCollectionDetailSync(tabId, url) {
  const config = await getConfig();
  if (!config.autoSyncCollections || !isConfigured(config)) {
    return;
  }

  const page = await readCollectionDetailPage({ tabId, url, exec: execInTab });
  if (!page || !page.found) {
    console.error("[collection detail auto-sync] couldn't read this collection");
    return;
  }
  const confirmedEmpty = page.rawLength === 0 && page.count === 0;
  if (page.items.length === 0 && !confirmedEmpty) {
    return;
  }

  const listId = page.parsed.id;
  const itemsHash = await hashCollectionItemsPayload(page.items);
  const state = await getState();
  if (findLastCollectionItemsHash(state.lastCollectionItemsHash, listId) === itemsHash) {
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
      report: logReport("collection detail auto-sync"),
    });
  } catch {
    return;
  }

  if (!result.persistable) {
    return;
  }
  await setState((current) => ({
    lastCollectionItemsHash: upsertCollectionItemsHash(current.lastCollectionItemsHash, listId, itemsHash),
  }));
}

/** Dispatches the sync triggers for a tab URL (full load or SPA navigation). */
function handleTabUrl(tabId, url) {
  if (tabId === undefined || !url) {
    return;
  }
  let hostname;
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    return;
  }
  if (MAKERWORLD_HOSTS.has(hostname)) {
    runOnce("courier", runCourier).catch((err) => console.warn("courier failed", err));
  }
  if (isCollectionsPage(url)) {
    runOnce(`${tabId}:collections`, () => runCollectionsSync(tabId, url)).catch((err) =>
      console.warn("collections auto-sync failed", err),
    );
  }
  if (isCollectionDetailPage(url)) {
    runOnce(`${tabId}:collection-detail`, () => runCollectionDetailSync(tabId, url)).catch((err) =>
      console.warn("collection detail auto-sync failed", err),
    );
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete") {
    handleTabUrl(tabId, tab && tab.url);
  }
});

// MakerWorld is a Next.js SPA: navigating to a collection never fires a
// `status: "complete"` tab update.
chrome.webNavigation?.onHistoryStateUpdated.addListener(
  (details) => {
    if (details.frameId === 0) {
      handleTabUrl(details.tabId, details.url);
    }
  },
  { url: [{ hostSuffix: "makerworld.com" }] },
);

// Primary courier trigger: the session cookie changed (sign-in, refresh).
chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (!removed && cookie && cookie.name === "token" && String(cookie.domain).replace(/^\./, "").endsWith("makerworld.com")) {
    runOnce("courier", runCourier).catch((err) => console.warn("courier failed", err));
  }
});

// Backstop for a cookie change that happened while the worker was asleep.
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === COURIER_ALARM_NAME) {
    runOnce("courier", runCourier).catch((err) => console.warn("courier failed", err));
  }
});
