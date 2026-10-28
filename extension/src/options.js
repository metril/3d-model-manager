/**
 * Options page. Loads/saves config over `chrome.storage.local` (via
 * `config.js`), requests the runtime host permission for the app's origin
 * (an optional host permission, so the user's app address isn't hard-coded),
 * and offers a "Test connection" check against `GET /ext/me`.
 */

import { renderGrantAccess } from "./access.js";
import { createClient } from "./api.js";
import { getConfig, hashResetPatch, setConfig, setState } from "./config.js";
import { describeMe, normalizeBaseUrl } from "./optionsLogic.js";

const baseUrlInput = document.getElementById("app-base-url");
const tokenInput = document.getElementById("api-token");
const autoCourierInput = document.getElementById("auto-courier");
const autoSyncCollectionsInput = document.getElementById("auto-sync-collections");
const statusEl = document.getElementById("status");
const saveButton = document.getElementById("save");
const testButton = document.getElementById("test");
const insecureEl = document.getElementById("insecure-warning");
const accessEl = document.getElementById("access");

function setStatus(text, kind) {
  statusEl.textContent = text || "";
  statusEl.className = kind ? `status ${kind}` : "status";
}

/** Shows the cleartext-http warning banner when the typed URL warrants it. */
function updateInsecureWarning() {
  const parsed = normalizeBaseUrl(baseUrlInput.value);
  insecureEl.hidden = !(parsed && parsed.insecure);
}

/** Requests the `<origin>/*` optional host permission, prompting the user. */
async function ensureOriginPermission(baseUrl) {
  const origin = new URL(baseUrl).origin;
  return chrome.permissions.request({ origins: [`${origin}/*`] });
}

async function refreshAccess() {
  accessEl.replaceChildren();
  const config = await getConfig();
  if (config.appBaseUrl) {
    await renderGrantAccess(accessEl, config.appBaseUrl, refreshAccess);
  }
}

async function load() {
  const config = await getConfig();
  baseUrlInput.value = config.appBaseUrl || "";
  tokenInput.value = config.apiToken || "";
  autoCourierInput.checked = config.autoCourier !== false;
  autoSyncCollectionsInput.checked = config.autoSyncCollections !== false;
  updateInsecureWarning();
  await refreshAccess();
}

baseUrlInput.addEventListener("input", updateInsecureWarning);

saveButton.addEventListener("click", async () => {
  const parsed = normalizeBaseUrl(baseUrlInput.value);
  if (!parsed) {
    setStatus("Enter a valid app address (e.g. http://nas.local:8080).", "error");
    return;
  }
  const token = tokenInput.value.trim();
  if (!token) {
    setStatus("Enter your API token.", "error");
    return;
  }

  saveButton.disabled = true;
  try {
    const granted = await ensureOriginPermission(parsed.url);
    if (!granted) {
      setStatus("Permission for your app's address is required so the extension can reach it.", "error");
      return;
    }
    const prev = await getConfig();
    await setConfig({
      appBaseUrl: parsed.url,
      apiToken: token,
      autoCourier: autoCourierInput.checked,
      autoSyncCollections: autoSyncCollectionsInput.checked,
    });
    const reset = hashResetPatch(prev, { appBaseUrl: parsed.url, apiToken: token });
    if (Object.keys(reset).length > 0) {
      await setState(reset);
    }
    setStatus(parsed.insecure ? "Saved (over plain http: your token is sent unencrypted)." : "Saved.", parsed.insecure ? "warn" : "ok");
    await refreshAccess();
  } catch (err) {
    setStatus(`Couldn't save: ${err?.message || "unknown error"}`, "error");
  } finally {
    saveButton.disabled = false;
  }
});

testButton.addEventListener("click", async () => {
  const parsed = normalizeBaseUrl(baseUrlInput.value);
  if (!parsed) {
    setStatus("Enter a valid app address first.", "error");
    return;
  }
  const token = tokenInput.value.trim();
  if (!token) {
    setStatus("Enter your API token first.", "error");
    return;
  }

  testButton.disabled = true;
  setStatus("Testing…", null);
  try {
    const granted = await ensureOriginPermission(parsed.url);
    if (!granted) {
      setStatus("Permission for your app's address is required so the extension can reach it.", "error");
      return;
    }
    const client = createClient({ baseUrl: parsed.url, token });
    const { text, kind } = describeMe(await client.me());
    setStatus(text, kind);
  } catch (err) {
    setStatus(`Test failed: ${err?.message || "unknown error"}`, "error");
  } finally {
    testButton.disabled = false;
  }
});

load().catch((err) => setStatus(`Couldn't load settings: ${err?.message || "unknown error"}`, "error"));
