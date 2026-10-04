/**
 * Options page. Loads/saves config over `chrome.storage.local` (via
 * `config.js`), requests the runtime host permission for the app's origin
 * (declared as `optional_host_permissions` in the manifest so we don't
 * hard-code the user's app address), and offers a "Test connection" ping.
 */

import { getConfig, hashResetPatch, setConfig } from "./config.js";
import { createClient } from "./api.js";

const baseUrlInput = document.getElementById("app-base-url");
const tokenInput = document.getElementById("api-token");
const autoCourierInput = document.getElementById("auto-courier");
const autoSyncCollectionsInput = document.getElementById("auto-sync-collections");
const statusEl = document.getElementById("status");
const saveButton = document.getElementById("save");
const testButton = document.getElementById("test");

function setStatus(text, kind) {
  statusEl.textContent = text || "";
  statusEl.className = kind ? `status ${kind}` : "status";
}

/** Trims a trailing slash and validates it parses as an http(s) URL. */
function normalizeBaseUrl(raw) {
  const trimmed = (raw || "").trim().replace(/\/+$/, "");
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return trimmed;
  } catch {
    return null;
  }
}

/** Requests the `<origin>/*` optional host permission, prompting the user. */
async function ensureOriginPermission(baseUrl) {
  const origin = new URL(baseUrl).origin;
  return chrome.permissions.request({ origins: [`${origin}/*`] });
}

async function load() {
  const config = await getConfig();
  baseUrlInput.value = config.appBaseUrl || "";
  tokenInput.value = config.apiToken || "";
  autoCourierInput.checked = config.autoCourier !== false;
  autoSyncCollectionsInput.checked = config.autoSyncCollections !== false;
}

saveButton.addEventListener("click", async () => {
  const baseUrl = normalizeBaseUrl(baseUrlInput.value);
  if (!baseUrl) {
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
    const granted = await ensureOriginPermission(baseUrl);
    if (!granted) {
      setStatus(
        "Permission for your app's address is required so the extension can reach it.",
        "error"
      );
      return;
    }
    const prev = await getConfig();
    await setConfig({
      ...hashResetPatch(prev, { appBaseUrl: baseUrl, apiToken: token }),
      appBaseUrl: baseUrl,
      apiToken: token,
      autoCourier: autoCourierInput.checked,
      autoSyncCollections: autoSyncCollectionsInput.checked,
    });
    setStatus("Saved.", "ok");
  } finally {
    saveButton.disabled = false;
  }
});

testButton.addEventListener("click", async () => {
  const baseUrl = normalizeBaseUrl(baseUrlInput.value);
  if (!baseUrl) {
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
    const granted = await ensureOriginPermission(baseUrl);
    if (!granted) {
      setStatus(
        "Permission for your app's address is required so the extension can reach it.",
        "error"
      );
      return;
    }
    const client = createClient({ baseUrl, token });
    const result = await client.ping();
    setStatus(
      result.ok ? "Connected." : `Failed (${result.status || "network error"}): ${result.error}`,
      result.ok ? "ok" : "error"
    );
  } finally {
    testButton.disabled = false;
  }
});

load();
