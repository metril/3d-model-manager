/**
 * Options page. Loads/saves config over `chrome.storage.local` (via
 * `config.js`), requests the runtime host permission for the app's origin
 * (an optional host permission, so the user's app address isn't hard-coded),
 * and runs the diagnostics check against `GET /ext/me` on Save and on "Test
 * connection": signed-in user, app version, missing-permission warnings and
 * an app-vs-extension version-mismatch notice. The last result is cached in
 * `storage.session` for the popup's footer. The optional "Printer key" is
 * validated against `GET /ext/v2/ping` (needs the `queue` + `read` scopes).
 */

import { renderGrantAccess } from "./access.js";
import { createClient } from "./api.js";
import { getConfig, hashResetPatch, setConfig, setState } from "./config.js";
import { describeMe, describePrinterKey, normalizeBaseUrl, settingsTokensUrl } from "./optionsLogic.js";

const baseUrlInput = document.getElementById("app-base-url");
const tokenInput = document.getElementById("api-token");
const printerKeyInput = document.getElementById("printer-key");
const autoCourierInput = document.getElementById("auto-courier");
const autoSyncCollectionsInput = document.getElementById("auto-sync-collections");
const statusEl = document.getElementById("status");
const diagnosticsEl = document.getElementById("diagnostics");
const saveButton = document.getElementById("save");
const testButton = document.getElementById("test");
const insecureEl = document.getElementById("insecure-warning");
const accessEl = document.getElementById("access");
const tokensLink = document.getElementById("tokens-link");

function setStatus(text, kind) {
  statusEl.textContent = text || "";
  statusEl.className = kind ? `status ${kind}` : "status";
  // Errors are announced assertively, everything else politely.
  statusEl.setAttribute("role", kind === "error" ? "alert" : "status");
}

/** Shows the cleartext-http warning banner when the typed URL warrants it. */
function updateInsecureWarning() {
  const parsed = normalizeBaseUrl(baseUrlInput.value);
  insecureEl.hidden = !(parsed && parsed.insecure);
}

/** Deep-links the token hint to the app's Settings -> Accounts tab once a URL is typed. */
function updateTokensLink() {
  const parsed = normalizeBaseUrl(baseUrlInput.value);
  if (parsed) {
    tokensLink.href = settingsTokensUrl(parsed.url);
  } else {
    tokensLink.removeAttribute("href");
  }
}

/** Wires a Show/Hide button to a password input. */
function bindToggle(buttonId, input) {
  const button = document.getElementById(buttonId);
  button.addEventListener("click", () => {
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.textContent = reveal ? "Hide" : "Show";
    button.setAttribute("aria-pressed", String(reveal));
  });
}
bindToggle("toggle-token", tokenInput);
bindToggle("toggle-printer-key", printerKeyInput);

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
  printerKeyInput.value = config.printerKey || "";
  autoCourierInput.checked = config.autoCourier !== false;
  autoSyncCollectionsInput.checked = config.autoSyncCollections !== false;
  updateInsecureWarning();
  updateTokensLink();
  await refreshAccess();
}

baseUrlInput.addEventListener("input", () => {
  updateInsecureWarning();
  updateTokensLink();
});

/** Renders the diagnostics block (user, version, notices) and caches `/me` for the popup. */
function renderDiagnostics(me, described) {
  const lines = [];
  if (me.ok && me.data && me.data.username) {
    lines.push(`Signed in as ${me.data.username}${me.data.app_version ? ` · app v${me.data.app_version}` : ""}`);
  }
  lines.push(...described.notices);
  diagnosticsEl.replaceChildren(
    ...lines.map((line, i) => {
      const p = document.createElement("p");
      p.style.margin = "0";
      p.textContent = line;
      if (i > 0) p.className = "status warn";
      return p;
    }),
  );
  diagnosticsEl.hidden = lines.length === 0;
  const cache = me.ok && me.data && me.data.username ? me.data : null;
  const session = chrome.storage.session;
  Promise.resolve(cache ? session.set({ "me-cache": cache }) : session.remove("me-cache")).catch(() => {});
}

/** `/me` check with the token as typed; returns the described result. */
async function checkConnection(url, token) {
  const client = createClient({ baseUrl: url, token });
  const me = await client.me();
  const described = describeMe(me, chrome.runtime.getManifest().version);
  renderDiagnostics(me, described);
  return described;
}

/** Validates the typed printer key; `null` when the field is empty. */
async function checkPrinterKey(url, key) {
  if (!key) {
    return null;
  }
  return describePrinterKey(await createClient({ baseUrl: url, token: key }).v2Ping());
}

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
    const typedKey = printerKeyInput.value.trim();
    const keyCheck = await checkPrinterKey(parsed.url, typedKey);
    await setConfig({
      appBaseUrl: parsed.url,
      apiToken: token,
      // An invalid key is not stored (it would add dead controls); empty clears it.
      printerKey: keyCheck ? (keyCheck.ok ? typedKey : prev.printerKey) : "",
      autoCourier: autoCourierInput.checked,
      autoSyncCollections: autoSyncCollectionsInput.checked,
    });
    const reset = hashResetPatch(prev, { appBaseUrl: parsed.url, apiToken: token });
    if (Object.keys(reset).length > 0) {
      await setState(reset);
    }
    const described = await checkConnection(parsed.url, token);
    if (keyCheck && !keyCheck.ok) {
      printerKeyInput.value = prev.printerKey;
      setStatus(`Saved, but ${keyCheck.text}`, "error");
    } else if (described.kind === "error") {
      setStatus(`Saved, but the check failed: ${described.text}`, "error");
    } else if (parsed.insecure) {
      setStatus("Saved (over plain http: your token is sent unencrypted).", "warn");
    } else {
      setStatus(`Saved. ${described.text}${keyCheck ? ` ${keyCheck.text}` : ""}`, described.kind);
    }
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
    const described = await checkConnection(parsed.url, token);
    const keyCheck = await checkPrinterKey(parsed.url, printerKeyInput.value.trim());
    setStatus(`${described.text}${keyCheck ? ` ${keyCheck.text}` : ""}`, keyCheck && !keyCheck.ok ? "error" : described.kind);
  } catch (err) {
    setStatus(`Test failed: ${err?.message || "unknown error"}`, "error");
  } finally {
    testButton.disabled = false;
  }
});

load().catch((err) => setStatus(`Couldn't load settings: ${err?.message || "unknown error"}`, "error"));
