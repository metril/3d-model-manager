import { test } from "node:test";
import assert from "node:assert/strict";

import { toSafari } from "../scripts/build-safari-manifest.mjs";
import { readFileSync } from "node:fs";
import { install } from "./chromeStub.js";

// Safari may lack cookies.onChanged / getAllCookieStores: nothing may throw.
const stub = install({
  scripting: { executeScript: async () => [{ result: null }] },
  cookies: {
    get: async () => ({ value: "cookie-1", domain: "makerworld.com" }),
    getAll: async () => [],
  },
});
globalThis.fetch = async () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({ ok: true }) });

test("background registers without cookies.onChanged; alarm backstop remains", async () => {
  await import("../src/background.js");
  assert.equal((stub.listeners["cookies.onChanged"] || []).length, 0);
  assert.ok((stub.listeners["alarms.onAlarm"] || []).length >= 1);
});

test("readMakerworldCookie works without getAllCookieStores", async () => {
  const { readMakerworldCookie } = await import("../src/courierRun.js");
  const c = await readMakerworldCookie();
  assert.equal(c?.value, "cookie-1");
});

test("readMakerworldCookie tolerates a missing cookies API", async () => {
  const saved = globalThis.chrome.cookies;
  delete globalThis.chrome.cookies;
  try {
    const { readMakerworldCookie } = await import("../src/courierRun.js");
    await readMakerworldCookie().catch(() => {});
  } finally {
    globalThis.chrome.cookies = saved;
  }
});

test("safari manifest: scripts module background, no unsupported keys", () => {
  const m = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
  const s = toSafari(m);
  assert.deepEqual(s.background.scripts, ["src/background.js"]);
  assert.equal(s.background.type, "module");
  assert.equal(s.background.service_worker, undefined);
  assert.equal(s.browser_specific_settings, undefined);
  assert.equal(s.minimum_chrome_version, undefined);
  assert.ok(!s.permissions.includes("webNavigation"));
  assert.ok(m.permissions.includes("webNavigation"));
});
