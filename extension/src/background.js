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
 *   - save: popup message / context menu. Both go through `startSave` +
 *     `trackImport` (`importTracker.js`): the worker owns the status poll,
 *     progress lives in `storage.session`, and a 1-minute alarm resumes any
 *     import that outlived one poll run (or a worker restart).
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
import { listActiveImports, patchImport, readImport, runQueue, startSave, trackImport } from "./importTracker.js";
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
const IMPORT_ALARM_NAME = "import-resume";
const QUEUE_PAGE_ID = "save-queue-page";
const QUEUE_LINK_ID = "save-queue-link";
const BADGE_ERROR_COLOR = "#b3261e";

/**
 * "Save & queue" entries exist only while a printer key is configured, so
 * there is never a menu item that cannot work. Rebuilt on every config change.
 */
export async function ensureContextMenu() {
  const config = await getConfig().catch(() => null);
  const queue = Boolean(config && config.printerKey);
  // Two separate items per action, not one with both contexts: Chrome ANDs
  // `documentUrlPatterns` and `targetUrlPatterns` on a single item, which
  // would hide the link entry on non-gallery pages.
  chrome.contextMenus.removeAll(() => {
    const items = [
      [CONTEXT_MENU_PAGE_ID, "Save model to my library", "page"],
      [CONTEXT_MENU_LINK_ID, "Save model to my library", "link"],
      ...(queue
        ? [
            [QUEUE_PAGE_ID, "Save & queue to printer", "page"],
            [QUEUE_LINK_ID, "Save & queue to printer", "link"],
          ]
        : []),
    ];
    for (const [id, title, context] of items) {
      chrome.contextMenus.create({
        id,
        title,
        contexts: [context],
        ...(context === "page"
          ? { documentUrlPatterns: GALLERY_HOST_PATTERNS }
          : { targetUrlPatterns: GALLERY_HOST_PATTERNS }),
      });
    }
  });
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes.config) {
    return;
  }
  const before = changes.config.oldValue?.printerKey || "";
  const after = changes.config.newValue?.printerKey || "";
  if (Boolean(before) !== Boolean(after)) {
    ensureContextMenu().catch((err) => console.warn("context menu rebuild failed", err));
  }
});

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
  resumeImports().catch((err) => console.warn("import resume failed", err));
  // Clear any stale global badge left by a previous run.
  Promise.resolve(chrome.action.setBadgeText({ text: "" })).catch(() => {});
}
onWorkerStart();

chrome.runtime.onInstalled.addListener(() => {
  ensureContextMenu();
  onWorkerStart();
});
chrome.runtime.onStartup.addListener(onWorkerStart);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Tracker dependencies for the current config (the printer key is optional). */
function trackerDeps(config) {
  const base = { baseUrl: config.appBaseUrl };
  return {
    api: createClient({ ...base, token: config.apiToken }),
    v2Api: config.printerKey ? createClient({ ...base, token: config.printerKey }) : null,
    setBadge: (tabId, text, color) => flashBadge(text, color, tabId ?? undefined),
    getState,
    sleep,
  };
}

/** Starts the tracked poll for an import (single-flight per id). */
function kickTrack(id, config) {
  Promise.resolve(chrome.alarms.create(IMPORT_ALARM_NAME, { periodInMinutes: 1 })).catch(() => {});
  runOnce(`import:${id}`, () => trackImport(id, trackerDeps(config)))
    .then(() => finishAlarmIfIdle())
    .catch((err) => console.warn("import tracking failed", err));
}

/** Drops the resume alarm once no import is unfinished. */
async function finishAlarmIfIdle() {
  if ((await listActiveImports()).length === 0) {
    await chrome.alarms.clear(IMPORT_ALARM_NAME);
  }
}

/** Resumes every unfinished import (alarm tick / worker start). */
async function resumeImports() {
  const active = await listActiveImports();
  if (active.length === 0) {
    return;
  }
  const config = await getConfig();
  if (!isConfigured(config)) {
    return;
  }
  for (const rec of active) {
    kickTrack(rec.id, config);
  }
}

/**
 * Shared "save this URL" flow (popup message and context menu). Never
 * throws. Returns right after the import row exists; progress is tracked in
 * the background (see `importTracker.js`).
 * @param {string} url
 * @param {{tabId?: number, options?: object, queue?: {printerId: number|null}|null}} [extra]
 */
async function saveUrl(url, { tabId, options, queue = null } = {}) {
  const config = await getConfig();
  if (!isConfigured(config)) {
    return { needsConfig: true };
  }
  if (queue && !config.printerKey) {
    return { ok: false, status: 0, data: null, error: "Set a printer key in the extension options first." };
  }
  const result = await startSave({ url, tabId, options, queue }, trackerDeps(config));
  if (result.importId !== undefined) {
    kickTrack(result.importId, config);
  }
  return result;
}

/** Popup "Add to queue" after the import is done. */
async function queueImport(importId, printerId) {
  const config = await getConfig();
  if (!isConfigured(config) || !config.printerKey) {
    return { ok: false, error: "Set a printer key in the extension options first." };
  }
  const rec = await readImport(importId);
  if (!rec) {
    return { ok: false, error: "That import is no longer tracked." };
  }
  await patchImport(importId, { queue: { printerId }, queueResult: null });
  await runQueue(importId, trackerDeps(config));
  return { ok: true };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!isOwnExtensionSender(sender, chrome.runtime)) {
    return false; // only our own extension pages may trigger a save
  }
  const fail = (err) => sendResponse({ ok: false, error: err?.message || "Save failed" });
  if (message && message.type === "save" && typeof message.url === "string") {
    if (!isModelPage(message.url)) {
      sendResponse({ ok: false, error: "That isn't a supported model page." });
      return false;
    }
    saveUrl(message.url, { tabId: message.tabId, options: message.options }).then(sendResponse).catch(fail);
    return true; // keep the channel open for the async response
  }
  if (message && message.type === "queue" && message.importId !== undefined) {
    const printerId = Number.isInteger(message.printerId) ? message.printerId : null;
    queueImport(message.importId, printerId).then(sendResponse).catch(fail);
    return true;
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
    const queue = info.menuItemId === QUEUE_PAGE_ID || info.menuItemId === QUEUE_LINK_ID;
    if (!queue && info.menuItemId !== CONTEXT_MENU_PAGE_ID && info.menuItemId !== CONTEXT_MENU_LINK_ID) {
      return;
    }
    const url = info.linkUrl || info.pageUrl;
    if (!url || !isModelPage(url)) {
      await flashBadge("!", BADGE_ERROR_COLOR, tabId);
      return;
    }
    // Progress/result badges are set by the tracker (same path as the popup).
    const result = await saveUrl(url, { tabId, queue: queue ? { printerId: null } : null });
    if (result && result.ok) {
      return;
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
  if (alarm.name === IMPORT_ALARM_NAME) {
    resumeImports().catch((err) => console.warn("import resume failed", err));
  } else if (alarm.name === COURIER_ALARM_NAME) {
    runOnce("courier", runCourier).catch((err) => console.warn("courier failed", err));
  }
});
