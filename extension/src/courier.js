/**
 * Pure-ish cookie-courier helpers. Uses only Web Crypto (`crypto.subtle`,
 * available both in the MV3 service worker and in Node 20+), no `chrome.*`.
 *
 * We store only a HASH of the last-pushed cookie value in extension
 * storage (never the raw cookie) so the extension doesn't keep a second
 * copy of the secret at rest — `shouldPush` diffs the *current* cookie
 * against that hash to decide whether a re-push is needed.
 */

/**
 * SHA-256 hash of `value`, returned as a lowercase hex string.
 * @param {string} value
 * @returns {Promise<string>}
 */
export async function hashToken(value) {
  const bytes = new TextEncoder().encode(value ?? "");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * True when `currentValue` is non-empty and its hash differs from
 * `lastPushedHash` (i.e. the cookie is new or has changed since the last
 * successful courier push).
 * @param {string|null|undefined} currentValue
 * @param {string|null|undefined} lastPushedHash
 * @returns {Promise<boolean>}
 */
export async function shouldPush(currentValue, lastPushedHash) {
  if (!currentValue) {
    return false;
  }
  const currentHash = await hashToken(currentValue);
  return currentHash !== lastPushedHash;
}

/**
 * Hashes `value` ONCE and compares with the last pushed hash. Returns the
 * hash so the caller can persist it without hashing again.
 * @param {string|null|undefined} value
 * @param {string|null|undefined} lastPushedHash
 * @returns {Promise<{push: boolean, hash: string|null}>}
 */
export async function decidePush(value, lastPushedHash) {
  if (!value) {
    return { push: false, hash: null };
  }
  const hash = await hashToken(value);
  return { push: hash !== lastPushedHash, hash };
}

const stripDot = (domain) => String(domain || "").replace(/^\./, "").toLowerCase();

/**
 * Deterministically picks the best cookie from a `cookies.get`/`getAll`
 * result: skips expired cookies and empty values, then prefers (1) the exact
 * apex domain (`makerworld.com`), (2) `hostOnly`, (3) the latest expiry
 * (session cookies, which have none, rank below persistent ones), and finally
 * breaks ties on the value so the choice never depends on array order.
 * Pure/chrome-free.
 * @param {Array<{value?: string, domain?: string, hostOnly?: boolean, expirationDate?: number}>|null|undefined} cookies
 * @param {string} [exactDomain]
 * @param {number} [nowSeconds]
 * @returns {object|null} the chosen cookie object
 */
export function pickCookie(cookies, exactDomain = "makerworld.com", nowSeconds = Date.now() / 1000) {
  if (!Array.isArray(cookies)) {
    return null;
  }
  const usable = cookies.filter(
    (c) =>
      c &&
      typeof c.value === "string" &&
      c.value !== "" &&
      !(typeof c.expirationDate === "number" && c.expirationDate <= nowSeconds),
  );
  usable.sort((a, b) => {
    const exact = Number(stripDot(b.domain) === exactDomain) - Number(stripDot(a.domain) === exactDomain);
    if (exact) return exact;
    const host = Number(Boolean(b.hostOnly)) - Number(Boolean(a.hostOnly));
    if (host) return host;
    const expiry = (b.expirationDate ?? 0) - (a.expirationDate ?? 0);
    if (expiry) return expiry;
    return a.value < b.value ? -1 : a.value > b.value ? 1 : 0;
  });
  return usable[0] ?? null;
}

/**
 * Value of the cookie `pickCookie` chooses, or `null`.
 * @param {Array<{value?: string}>|null|undefined} cookies
 * @returns {string|null}
 */
export function pickCookieValue(cookies) {
  return pickCookie(cookies)?.value ?? null;
}
