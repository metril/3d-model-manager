/**
 * Popup UI. Reads config + the active tab, then renders one of five
 * screens (`popupView`, `popupLogic.js`): "set up the extension", "other
 * page" (app link + supported sites), "save this model" (with collection and
 * tag options), "sync collections" (MakerWorld collections LIST page) or
 * "sync this collection" (collection DETAIL page). All the `chrome.*` calls
 * live here; the pure logic lives in `popupLogic.js`/`saveOptions.js`.
 *
 * Saving does NOT poll here: the background worker owns the import's status
 * poll (`importTracker.js`) and mirrors progress into `storage.session`; this
 * popup just subscribes via `storage.onChanged` and re-renders, so closing
 * and reopening it mid-import shows live state again. With a printer key
 * configured, a finished import offers "Add to queue".
 */

import { renderGrantAccess } from "./access.js";
import { createClient } from "./api.js";
import { getConfig, getState, isConfigured, setState } from "./config.js";
import { isModelPage } from "./detect.js";
import { execInTab } from "./exec.js";
import { IMPORT_PREFIX, findImportForUrl, readImport } from "./importTracker.js";
import { SUPPORTED_SITES, importView, popupView } from "./popupLogic.js";
import { buildSaveOptions, loadOptionLists, normalizeTags, splitTagInput } from "./saveOptions.js";
import { syncCollectionDetail, syncCollections } from "./syncFlow.js";

const $ = (id) => document.getElementById(id);
const messageEl = $("message");
const actionsEl = $("actions");
const statusEl = $("status");
const errorEl = $("error");
const courierEl = $("courier");

let tabId;
let unsubscribe = null;

/** Errors go to the `role="alert"` line (announced), everything else to the polite status. */
function setStatus(text, kind) {
  if (kind === "error") {
    errorEl.textContent = text || "";
    statusEl.textContent = "";
    statusEl.className = "status";
    return;
  }
  errorEl.textContent = "";
  statusEl.textContent = text || "";
  statusEl.className = kind ? `status ${kind}` : "status";
}

function makeButton(text, onClick, { primary = true } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = primary ? "primary" : "";
  button.textContent = text;
  button.addEventListener("click", onClick);
  return button;
}

function show(button) {
  actionsEl.replaceChildren(button);
  button.focus();
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
  link.focus();
}

/** Not a savable/syncable page: point at the app and the supported sites. */
function renderOther(config) {
  messageEl.textContent = "Open a model page on one of these sites to save it:";
  const list = document.createElement("ul");
  list.className = "sites";
  for (const site of SUPPORTED_SITES) {
    const item = document.createElement("li");
    item.textContent = site;
    list.append(item);
  }
  const link = document.createElement("a");
  link.className = "button primary";
  link.href = config.appBaseUrl;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = "Open my library";
  actionsEl.replaceChildren(list, link);
  link.focus();
}

// --- save options -----------------------------------------------------------

let tags = [];
const collectionEl = $("collection");
const tagInput = $("tag-input");
const chipsEl = $("tag-chips");

function renderChips() {
  chipsEl.replaceChildren(
    ...tags.map((tag) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.append(tag);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove tag ${tag}`);
      remove.addEventListener("click", () => {
        tags = tags.filter((t) => t !== tag);
        renderChips();
      });
      chip.append(remove);
      return chip;
    }),
  );
}

function commitTagInput() {
  const added = splitTagInput(tagInput.value);
  tagInput.value = "";
  tags = normalizeTags([...tags, ...added]);
  renderChips();
}

tagInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === ",") {
    event.preventDefault();
    commitTagInput();
  } else if (event.key === "Backspace" && tagInput.value === "" && tags.length > 0) {
    tags = tags.slice(0, -1);
    renderChips();
  }
});
tagInput.addEventListener("change", commitTagInput);

/** Fills the pickers from the cached lists; the form works even if this fails. */
async function setupSaveOptions(client) {
  $("save-options").hidden = false;
  const { lastSave } = await getState();
  tags = normalizeTags(lastSave.tags);
  renderChips();
  const lists = await loadOptionLists(client);
  collectionEl.replaceChildren(collectionEl.options[0]);
  for (const c of lists.collections) {
    const option = document.createElement("option");
    option.value = String(c.id);
    option.textContent = c.name;
    collectionEl.append(option);
  }
  // Remembered choice, only if that collection still exists.
  if (lastSave.collectionId !== null && lists.collections.some((c) => c.id === lastSave.collectionId)) {
    collectionEl.value = String(lastSave.collectionId);
  }
  $("tag-list").replaceChildren(
    ...lists.tags.map((name) => {
      const option = document.createElement("option");
      option.value = name;
      return option;
    }),
  );
}

function renderSavable(url, config, client) {
  messageEl.textContent = "Send this model to your library.";
  const button = makeButton("Save to my library", () => handleSave(button, url, config));
  show(button);
  setupSaveOptions(client).catch(() => {});
  // A save already in flight (or finished) for this page: show its live state.
  findImportForUrl(url)
    .then((rec) => rec && watchImport(rec.id, config))
    .catch(() => {});
}

// --- import progress --------------------------------------------------------

const progressEl = $("progress");
const importStatusEl = $("import-status");
const openAppEl = $("open-app");
const queueBoxEl = $("queue-box");
const queueNoteEl = $("queue-note");
const printerEl = $("printer");
const queueButton = $("queue-button");
let printersLoaded = false;

async function loadPrinters(config) {
  if (printersLoaded) return;
  printersLoaded = true;
  const v2 = createClient({ baseUrl: config.appBaseUrl, token: config.printerKey });
  const [res, { lastSave }] = await Promise.all([v2.v2Printers(), getState()]);
  if (!res.ok) {
    printersLoaded = false;
    queueNoteEl.textContent = res.error || "Couldn't list printers.";
    queueNoteEl.className = "queue-note error";
    return;
  }
  const list = Array.isArray(res.data) ? res.data.filter((p) => p.enabled !== false) : [];
  printerEl.replaceChildren(
    ...list.map((p) => {
      const option = document.createElement("option");
      option.value = String(p.id);
      option.textContent = p.name;
      return option;
    }),
  );
  if (list.some((p) => p.id === lastSave.printerId)) {
    printerEl.value = String(lastSave.printerId);
  }
  // No visible printer (key without an allowlist): queue unassigned.
  printerEl.hidden = list.length === 0;
  document.querySelector('label[for="printer"]').hidden = list.length === 0;
}

function renderImport(rec, config) {
  const view = importView(rec, { base: config.appBaseUrl });
  progressEl.hidden = !rec;
  importStatusEl.textContent = view.text;
  importStatusEl.className = `status ${view.kind === "progress" ? "" : view.kind}`.trim();
  openAppEl.hidden = !view.openUrl;
  if (view.openUrl) {
    openAppEl.href = view.openUrl;
  }
  queueNoteEl.textContent = view.queueText || "";
  queueNoteEl.className = view.queueKind ? `queue-note ${view.queueKind}` : "queue-note";
  const canQueue = view.done && Boolean(config.printerKey) && !["queued", "none", "multiple"].includes(rec.queueResult?.status);
  queueBoxEl.hidden = !canQueue;
  queueButton.disabled = false;
  if (canQueue) {
    loadPrinters(config).catch(() => {});
  }
}

/** Renders the stored record now and again on every change to it. */
async function watchImport(id, config) {
  unsubscribe?.();
  const key = IMPORT_PREFIX + id;
  const listener = (changes, area) => {
    if (area === "session" && changes[key]) {
      renderImport(changes[key].newValue, config);
    }
  };
  chrome.storage.onChanged.addListener(listener);
  unsubscribe = () => chrome.storage.onChanged.removeListener(listener);
  renderImport(await readImport(id), config);
  queueButton.onclick = async () => {
    queueButton.disabled = true;
    const printerId = printerEl.hidden ? null : Number(printerEl.value);
    if (printerId !== null) {
      setState((s) => ({ lastSave: { ...s.lastSave, printerId } })).catch(() => {});
    }
    queueNoteEl.textContent = "Adding to the queue…";
    queueNoteEl.className = "queue-note";
    try {
      const res = await chrome.runtime.sendMessage({ type: "queue", importId: id, printerId });
      if (res && !res.ok) {
        setStatus(res.error || "Couldn't add to the queue.", "error");
        queueButton.disabled = false;
      }
    } catch {
      setStatus("Something went wrong.", "error");
      queueButton.disabled = false;
    }
  };
}

async function handleSave(button, url, config) {
  button.disabled = true;
  commitTagInput();
  const options = buildSaveOptions({ collectionId: collectionEl.value, tags });
  setState((s) => ({ lastSave: { ...s.lastSave, collectionId: options.collectionId, tags: options.tags } })).catch(() => {});
  setStatus("Saving…", null);
  let response;
  try {
    response = await chrome.runtime.sendMessage({ type: "save", url, tabId, options });
  } catch {
    // The background worker's message port can reject/close out from under
    // us (e.g. it was asleep and got killed again). Don't leave the button
    // stuck disabled on "Saving…".
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
  setStatus("", null);
  button.disabled = false;
  if (response.importId !== undefined) {
    await watchImport(response.importId, config);
  }
}

// --- collection sync --------------------------------------------------------

function renderSyncCollections(url, config) {
  messageEl.textContent = "Sync your MakerWorld collections into the app.";
  show(makeButton("Sync collections to app", (e) => handleSync(e.currentTarget, syncCollections, url, config)));
}

/**
 * M11: the collection DETAIL page's own "Sync this collection to app"
 * button -- a guaranteed-correct complement to the bulk button above, since
 * the user is looking straight at this collection's items.
 */
function renderSyncCollectionDetail(url, config) {
  messageEl.textContent = "Sync this collection into the app.";
  show(makeButton("Sync this collection to app", (e) => handleSync(e.currentTarget, syncCollectionDetail, url, config)));
}

/**
 * Delegates the whole read/push flow to the shared `syncFlow.js` function,
 * wiring its injected seams to `chrome.scripting`, `api.js` and the status
 * line. The flow already `report`s every outcome (failures included), so
 * all that is left here is the "Syncing…" up-front message and the button
 * state.
 */
async function handleSync(button, flow, url, config) {
  button.disabled = true;
  setStatus("Syncing…", null);
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  try {
    await flow({ tabId, url, exec: execInTab, api: client, report: setStatus });
  } catch {
    // Already reported via `setStatus` inside the flow.
  } finally {
    button.disabled = false;
  }
}

// --- footer -----------------------------------------------------------------

/** Shows the last courier outcome (recorded by the background worker). */
async function renderCourierStatus() {
  const state = await getState();
  if (!state.lastCourierStatus) {
    return;
  }
  const when = state.lastCourierAt ? ` (${new Date(state.lastCourierAt).toLocaleString()})` : "";
  courierEl.textContent = `Cookie sync: ${state.lastCourierStatus}${when}`;
}

/** Account + app version cached by the options page's last "/me" check. */
async function renderWhoami() {
  const me = (await chrome.storage.session.get("me-cache"))["me-cache"];
  if (!me || !me.username) {
    return;
  }
  const el = $("whoami");
  el.textContent = `Signed in as ${me.username}${me.app_version ? ` · app v${me.app_version}` : ""}`;
  el.hidden = false;
}

async function init() {
  const config = await getConfig();
  const [tab] = isConfigured(config) ? await chrome.tabs.query({ active: true, currentWindow: true }) : [];
  tabId = tab && tab.id;
  const url = tab && tab.url;
  const view = popupView({ configured: isConfigured(config), url, hasTab: tabId !== undefined });
  if (view === "setup") {
    renderSetup();
    return;
  }
  // Drop any stale per-tab badge left by a previous save.
  if (tabId !== undefined) {
    Promise.resolve(chrome.action.setBadgeText({ text: "", tabId })).catch(() => {});
  }
  renderCourierStatus().catch(() => {});
  renderWhoami().catch(() => {});
  renderGrantAccess($("access"), config.appBaseUrl, () => init()).catch(() => {});
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  if (view === "model" && isModelPage(url)) {
    renderSavable(url, config, client);
  } else if (view === "collections") {
    renderSyncCollections(url, config);
  } else if (view === "collection-detail") {
    renderSyncCollectionDetail(url, config);
  } else {
    renderOther(config);
  }
}

init().catch((err) => setStatus(err?.message || "Something went wrong.", "error"));
