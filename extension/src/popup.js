/**
 * Popup UI. Reads config + the active tab, then renders one of five
 * states: "set up the extension", "not a model page", "save this model",
 * "sync collections" (on a MakerWorld collections LIST page), or "sync this
 * collection" (M11, on a collection DETAIL page). All the `chrome.*` calls
 * live here; the URL detection logic they feed is
 * `isModelPage`/`isCollectionsPage`/`isCollectionDetailPage` (`detect.js`).
 * Both "sync collections" buttons delegate the actual read/push flow to the
 * shared `syncFlow.js` module (import-health branch T5 -- extracted so the
 * background service worker can drive the identical flow for
 * auto-sync-on-visit, see `background.js`); this file's job is just to wire
 * that module's injected seams (`exec`, `api`, `report`) to
 * `chrome.scripting`/`api.js`/the status line and manage the button's
 * disabled state.
 *
 * After a save creates a new import (201), `handleSave` polls the real
 * outcome with `pollImportStatus` (`saveStatus.js`, import-health branch
 * T4) instead of stopping at "row created." The poll lives here, in the
 * popup, rather than the background service worker: it's a plain
 * `await`-in-a-click-handler loop, and since a closed popup simply stops
 * running (there's no message channel to keep it alive), a poll that's
 * mid-flight when the popup closes just stops -- acceptable, see the
 * README.
 */

import { isCollectionDetailPage, isCollectionsPage, isModelPage } from "./detect.js";
import { getConfig, isConfigured } from "./config.js";
import { createClient } from "./api.js";
import { syncCollectionDetail, syncCollections } from "./syncFlow.js";
import { pollImportStatus } from "./saveStatus.js";

const messageEl = document.getElementById("message");
const actionsEl = document.getElementById("actions");
const statusEl = document.getElementById("status");

function setStatus(text, kind) {
  statusEl.textContent = text || "";
  statusEl.className = kind ? `status ${kind}` : "status";
}

function renderSetup() {
  messageEl.textContent =
    "Set up the extension with your app's address and API token before saving models.";
  const link = document.createElement("a");
  link.className = "button primary";
  link.href = "#";
  link.textContent = "Set up the extension";
  link.addEventListener("click", (event) => {
    event.preventDefault();
    chrome.runtime.openOptionsPage();
  });
  actionsEl.replaceChildren(link);
}

function renderNotAModelPage() {
  messageEl.textContent = "This isn't a MakerWorld, Thingiverse, or Printables model page.";
  const button = document.createElement("button");
  button.textContent = "Save to my library";
  button.disabled = true;
  actionsEl.replaceChildren(button);
}

function renderSavable(url, config) {
  messageEl.textContent = "Send this model to your library.";
  const button = document.createElement("button");
  button.className = "primary";
  button.textContent = "Save to my library";
  button.addEventListener("click", () => handleSave(button, url, config));
  actionsEl.replaceChildren(button);
}

function renderSyncCollections(tabId, url, config) {
  messageEl.textContent = "Sync your MakerWorld collections into the app.";
  const button = document.createElement("button");
  button.className = "primary";
  button.textContent = "Sync collections to app";
  button.addEventListener("click", () => handleSyncCollections(button, tabId, url, config));
  actionsEl.replaceChildren(button);
}

/**
 * M11: the collection DETAIL page's own "Sync this collection to app"
 * button -- a guaranteed-correct complement to the bulk "Sync collections to
 * app" button above, since the user is looking straight at this
 * collection's items (`isCollectionDetailPage`, `detect.js`).
 */
function renderSyncCollectionDetail(tabId, url, config) {
  messageEl.textContent = "Sync this collection into the app.";
  const button = document.createElement("button");
  button.className = "primary";
  button.textContent = "Sync this collection to app";
  button.addEventListener("click", () => handleSyncCollectionDetail(button, tabId, url, config));
  actionsEl.replaceChildren(button);
}

/**
 * `exec(tabId, func, args)` seam `syncFlow.js` needs -- the one place this
 * file wraps `chrome.scripting.executeScript` for the collections flow.
 * Resolves to the injected function's return value (`results[0].result`),
 * or rejects if the injection itself fails (tab closed, no permission,
 * etc.) -- `syncFlow.js` is the layer that decides what an injection
 * failure means for the overall sync (see its docstring).
 */
async function execInTab(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return results && results[0] && results[0].result;
}

/**
 * Delegates the whole read/push flow to `syncCollections` (`syncFlow.js`),
 * wiring its injected seams to this popup's `chrome.scripting` access, its
 * `api.js` client, and the status line. `syncCollections` already calls
 * `report` with the same user-facing message for every outcome (including
 * failures) before resolving/rejecting, so the only thing left to do here
 * is manage the button's disabled state.
 */
async function handleSyncCollections(button, tabId, url, config) {
  button.disabled = true;
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  try {
    await syncCollections({ tabId, url, exec: execInTab, api: client, report: setStatus });
  } catch {
    // Already reported via `setStatus` inside `syncCollections` -- nothing
    // left to do here.
  } finally {
    button.disabled = false;
  }
}

/**
 * Mirrors `handleSyncCollections` above, delegating to `syncCollectionDetail`
 * (`syncFlow.js`) instead -- the single-collection, guaranteed-correct sync
 * for the detail page the user is currently on.
 */
async function handleSyncCollectionDetail(button, tabId, url, config) {
  button.disabled = true;
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  try {
    await syncCollectionDetail({ tabId, url, exec: execInTab, api: client, report: setStatus });
  } catch {
    // Already reported via `setStatus` inside `syncCollectionDetail` --
    // nothing left to do here.
  } finally {
    button.disabled = false;
  }
}

/** Real `setTimeout`-backed `sleep`, injected into `pollImportStatus`. */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function handleSave(button, url, config) {
  button.disabled = true;
  setStatus("Saving…", null);
  let response;
  try {
    response = await chrome.runtime.sendMessage({ type: "save", url });
  } catch {
    // The background service worker's message port can reject/close out
    // from under us (e.g. it was asleep and got killed again). Don't leave
    // the button stuck disabled on "Saving…" — restore it with a generic
    // error instead.
    setStatus("Something went wrong.", "error");
    button.disabled = false;
    return;
  }
  if (response && response.needsConfig) {
    setStatus("The extension isn't configured yet.", "error");
    button.disabled = false;
    return;
  }
  if (!response || !response.ok) {
    setStatus((response && response.error) || "Something went wrong.", "error");
    button.disabled = false;
    return;
  }
  if (response.status !== 201) {
    // Already in the library (200, deduped) -- nothing new was created, so
    // there's no import to poll.
    setStatus("Saved — already in your library.", "ok");
    return;
  }

  setStatus("Added to your library.", "ok");
  const importId = response.data && response.data.id;
  if (importId === undefined || importId === null) {
    return;
  }
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  const { outcome, error } = await pollImportStatus({
    fetchStatus: (id) => client.getImportStatus(id),
    importId,
    sleep,
  });
  if (outcome === "done") {
    setStatus("Imported ✓", "ok");
  } else if (outcome === "failed") {
    setStatus(error || "Import failed.", "error");
  } else {
    setStatus("Still importing — check the app.", null);
  }
}

async function init() {
  const config = await getConfig();
  if (!isConfigured(config)) {
    renderSetup();
    return;
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab && tab.url;
  if (url && isModelPage(url)) {
    renderSavable(url, config);
    return;
  }
  if (url && tab.id !== undefined && isCollectionsPage(url)) {
    renderSyncCollections(tab.id, url, config);
    return;
  }
  if (url && tab.id !== undefined && isCollectionDetailPage(url)) {
    renderSyncCollectionDetail(tab.id, url, config);
    return;
  }

  renderNotAModelPage();
}

init().catch((err) => setStatus(err?.message || "Something went wrong.", "error"));
