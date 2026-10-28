/**
 * Pure helpers for the options page (no DOM, no `chrome.*`) so they are
 * unit-testable.
 */

const REQUIRED_PERMS = ["library:write", "settings:write"];

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

/**
 * Turns an `api.me()` result into a status line for "Test connection".
 * @param {{ok: boolean, status: number, data: any, error: string|null}} result
 * @returns {{text: string, kind: "ok"|"warn"|"error"}}
 */
export function describeMe(result) {
  if (!result.ok) {
    return { text: `Failed (${result.status || "network error"}): ${result.error}`, kind: "error" };
  }
  const data = result.data || {};
  if (data.legacy || !data.username) {
    return { text: "Connected (older server: no account details available).", kind: "ok" };
  }
  const version = data.app_version ? ` · app v${data.app_version}` : "";
  const text = `Connected as ${data.username}${version}.`;
  if (Array.isArray(data.perms)) {
    const missing = REQUIRED_PERMS.filter((p) => !data.perms.includes(p));
    if (missing.length > 0) {
      return { text: `${text} Token is missing: ${missing.join(", ")}.`, kind: "warn" };
    }
  }
  return { text, kind: "ok" };
}
