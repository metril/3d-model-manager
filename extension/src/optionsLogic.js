/**
 * Pure helpers for the options page (no DOM, no `chrome.*`) so they are
 * unit-testable.
 */

const REQUIRED_PERMS = ["library:write"];

/** True for hosts where plain http is acceptable: localhost, RFC1918, `.local`. */
export function isPrivateHost(hostname) {
  const host = String(hostname || "").toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "localhost" || host === "::1" || host.endsWith(".localhost") || host.endsWith(".local")) {
    return true;
  }
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) {
    return false;
  }
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

/**
 * Trims a trailing slash and validates an http(s) URL. `insecure` is true for
 * plain http to a host that is NOT local/private, where the API token would
 * cross the network in cleartext (the options page shows a warning banner).
 * @param {string} raw
 * @returns {{url: string, insecure: boolean}|null}
 */
export function normalizeBaseUrl(raw) {
  const trimmed = (raw || "").trim().replace(/\/+$/, "");
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return { url: trimmed, insecure: parsed.protocol === "http:" && !isPrivateHost(parsed.hostname) };
  } catch {
    return null;
  }
}

/** Strips a leading "v" and whitespace so "v0.8.0" and "0.8.0" compare equal. */
const cleanVersion = (v) => String(v ?? "").trim().replace(/^v/i, "");

/**
 * Notice when the app and the extension run different versions (they ship
 * on one release train), or `null` when they match / either is unknown.
 */
export function versionMismatch(appVersion, manifestVersion) {
  const app = cleanVersion(appVersion);
  const ext = cleanVersion(manifestVersion);
  if (!app || !ext || app === ext) {
    return null;
  }
  return `The app is v${app} but this extension is v${ext}. They ship together, so update the older one.`;
}

/** Where the app's "Browser extension" token card lives (Settings -> Accounts tab). */
export function settingsTokensUrl(baseUrl) {
  return `${String(baseUrl ?? "").replace(/\/+$/, "")}/settings?tab=accounts`;
}

/**
 * Turns an `api.me()` result into a status line for "Test connection" and,
 * when `manifestVersion` is given, extra notices (version mismatch).
 * @param {{ok: boolean, status: number, data: any, error: string|null}} result
 * @param {string} [manifestVersion]
 * @returns {{text: string, kind: "ok"|"warn"|"error", notices: string[]}}
 */
export function describeMe(result, manifestVersion) {
  if (!result.ok) {
    return { text: `Failed (${result.status || "network error"}): ${result.error}`, kind: "error", notices: [] };
  }
  const data = result.data || {};
  if (data.legacy || !data.username) {
    return { text: "Connected (older server: no account details available).", kind: "ok", notices: [] };
  }
  const version = data.app_version ? ` · app v${cleanVersion(data.app_version)}` : "";
  const text = `Connected as ${data.username}${version}.`;
  const notices = [];
  const mismatch = manifestVersion ? versionMismatch(data.app_version, manifestVersion) : null;
  if (mismatch) {
    notices.push(mismatch);
  }
  if (Array.isArray(data.perms)) {
    const missing = REQUIRED_PERMS.filter((p) => !data.perms.includes(p));
    if (missing.length > 0) {
      return { text: `${text} Token is missing: ${missing.join(", ")}.`, kind: "warn", notices };
    }
  }
  // The token's perms are capped server-side, so settings:write never shows
  // there; newer servers report the real answer. Old servers: no field, no warning.
  if (data.can_set_credentials === false) {
    return {
      text: `${text} This token's owner can't set the MakerWorld cookie (needs settings:write).`,
      kind: "warn",
      notices,
    };
  }
  return { text, kind: notices.length > 0 ? "warn" : "ok", notices };
}

const PRINTER_KEY_SCOPES = ["queue", "read"];

/**
 * Validates a `GET /ext/v2/ping` result for the "Printer key" field: the key
 * must carry both the `queue` and `read` scopes.
 * @returns {{ok: boolean, text: string}}
 */
export function describePrinterKey(result) {
  if (!result.ok) {
    return { ok: false, text: `Printer key rejected (${result.status || "network error"}): ${result.error}` };
  }
  const scopes = Array.isArray(result.data?.key?.scopes) ? result.data.key.scopes : [];
  const missing = PRINTER_KEY_SCOPES.filter((s) => !scopes.includes(s));
  if (missing.length > 0) {
    return { ok: false, text: `Printer key is missing the ${missing.join(" and ")} scope${missing.length > 1 ? "s" : ""}.` };
  }
  const label = result.data?.key?.label;
  return { ok: true, text: label ? `Printer key "${label}" OK.` : "Printer key OK." };
}
