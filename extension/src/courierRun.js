/**
 * The MakerWorld cookie courier run, separated from `background.js` so it is
 * testable with a `chrome` stub. Reads the `token` cookie, and if it changed
 * since the last successful push (compared by hash -- the raw cookie is
 * never persisted) sends it to the app. Persists `lastCourierStatus` /
 * `lastCourierAt` so the popup can show what happened.
 */

import { createClient } from "./api.js";
import { getConfig, getState, isConfigured, setState } from "./config.js";
import { decidePush, pickCookie } from "./courier.js";

export const MAKERWORLD_COOKIE_NAME = "token";
export const MAKERWORLD_COOKIE_URL = "https://makerworld.com/";
export const MAKERWORLD_COOKIE_DOMAIN = "makerworld.com";

/** The default cookie store's id (differs by browser), or `undefined`. */
async function defaultStoreId() {
  try {
    const stores = await chrome.cookies.getAllCookieStores?.();
    return stores?.[0]?.id;
  } catch {
    return undefined;
  }
}

/**
 * Best cookie for the courier: the one the browser would actually send to
 * makerworld.com (`cookies.get`) when it is usable, else the deterministic
 * pick across `getAll`.
 * @returns {Promise<{value: string}|null>}
 */
export async function readMakerworldCookie() {
  const storeId = await defaultStoreId();
  const scope = storeId === undefined ? {} : { storeId };
  const nowSeconds = Date.now() / 1000;
  try {
    const direct = await chrome.cookies.get({ url: MAKERWORLD_COOKIE_URL, name: MAKERWORLD_COOKIE_NAME, ...scope });
    const picked = pickCookie(direct ? [direct] : [], MAKERWORLD_COOKIE_DOMAIN, nowSeconds);
    if (picked) {
      return picked;
    }
  } catch {
    // fall through to getAll
  }
  const all = await chrome.cookies.getAll({
    domain: MAKERWORLD_COOKIE_DOMAIN,
    name: MAKERWORLD_COOKIE_NAME,
    ...scope,
  });
  return pickCookie(all, MAKERWORLD_COOKIE_DOMAIN, nowSeconds);
}

async function recordStatus(status) {
  await setState({ lastCourierStatus: status, lastCourierAt: Date.now() });
}

/**
 * @returns {Promise<"disabled"|"no-cookie"|"unchanged"|"pushed"|"failed">}
 */
export async function runCourier() {
  const config = await getConfig();
  if (!config.autoCourier || !isConfigured(config)) {
    return "disabled";
  }
  const cookie = await readMakerworldCookie();
  if (!cookie) {
    await recordStatus("No MakerWorld cookie found — sign in to makerworld.com.");
    return "no-cookie";
  }
  const state = await getState();
  const { push, hash } = await decidePush(cookie.value, state.lastMakerworldHash);
  if (!push) {
    await recordStatus("Cookie up to date.");
    return "unchanged";
  }
  const client = createClient({ baseUrl: config.appBaseUrl, token: config.apiToken });
  const result = await client.setMakerworldCredential(cookie.value);
  if (result.ok) {
    await setState({ lastMakerworldHash: hash, lastCourierStatus: "Cookie sent to the app.", lastCourierAt: Date.now() });
    return "pushed";
  }
  await recordStatus(`Couldn't send the cookie: ${result.error}`);
  return "failed";
}
