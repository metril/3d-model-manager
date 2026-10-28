import { test } from "node:test";
import assert from "node:assert/strict";

import { getState, setConfig } from "../src/config.js";
import { hashToken } from "../src/courier.js";
import { runCourier } from "../src/courierRun.js";
import { install } from "./chromeStub.js";

const future = () => Date.now() / 1000 + 3600;

async function withEnv({ cookies, fetchImpl }, fn) {
  const getCalls = [];
  const h = install({
    cookies: {
      getAllCookieStores: async () => [{ id: "store-1" }],
      get: async (q) => {
        getCalls.push(["get", q]);
        return cookies.get ?? null;
      },
      getAll: async (q) => {
        getCalls.push(["getAll", q]);
        return cookies.getAll ?? [];
      },
      onChanged: { addListener() {} },
    },
  });
  const realFetch = globalThis.fetch;
  const fetchCalls = [];
  globalThis.fetch = async (url, init) => {
    fetchCalls.push({ url, init });
    return fetchImpl ? fetchImpl(url, init) : { ok: true, status: 200, json: async () => ({}) };
  };
  try {
    await setConfig({ appBaseUrl: "https://app.test", apiToken: "tok" });
    await fn({ getCalls, fetchCalls, h });
  } finally {
    globalThis.fetch = realFetch;
    h.uninstall();
  }
}

test("runCourier: uses cookies.get({url}) first with the default storeId, pushes, persists the hash and status", async () => {
  await withEnv({ cookies: { get: { value: "cookie-A", domain: ".makerworld.com", expirationDate: future() } } }, async ({ getCalls, fetchCalls }) => {
    assert.equal(await runCourier(), "pushed");
    assert.deepEqual(getCalls[0], ["get", { url: "https://makerworld.com/", name: "token", storeId: "store-1" }]);
    assert.equal(getCalls.length, 1); // getAll not needed
    assert.equal(fetchCalls.length, 1);
    assert.deepEqual(JSON.parse(fetchCalls[0].init.body), { token: "cookie-A" });
    const state = await getState();
    assert.equal(state.lastMakerworldHash, await hashToken("cookie-A"));
    assert.equal(state.lastCourierStatus, "Cookie sent to the app.");
    assert.equal(typeof state.lastCourierAt, "number");
  });
});

test("runCourier: an expired direct cookie is skipped and the deterministic getAll pick is used", async () => {
  await withEnv(
    {
      cookies: {
        get: { value: "expired", domain: "makerworld.com", expirationDate: 1 },
        getAll: [
          { value: "z-sub", domain: "www.makerworld.com", expirationDate: future() },
          { value: "apex", domain: ".makerworld.com", expirationDate: future() },
        ],
      },
    },
    async ({ fetchCalls }) => {
      assert.equal(await runCourier(), "pushed");
      assert.deepEqual(JSON.parse(fetchCalls[0].init.body), { token: "apex" });
    },
  );
});

test("runCourier: unchanged hash does not push; status says up to date", async () => {
  await withEnv({ cookies: { get: { value: "same", domain: "makerworld.com" } } }, async ({ fetchCalls }) => {
    assert.equal(await runCourier(), "pushed");
    assert.equal(await runCourier(), "unchanged");
    assert.equal(fetchCalls.length, 1);
    assert.equal((await getState()).lastCourierStatus, "Cookie up to date.");
  });
});

test("runCourier: no cookie records a status; a failed push keeps the old hash and records the error", async () => {
  await withEnv({ cookies: {} }, async () => {
    assert.equal(await runCourier(), "no-cookie");
    assert.match((await getState()).lastCourierStatus, /No MakerWorld cookie/);
  });
  await withEnv(
    {
      cookies: { get: { value: "c", domain: "makerworld.com" } },
      fetchImpl: async () => ({ ok: false, status: 500, statusText: "x", json: async () => ({ detail: "db down" }) }),
    },
    async () => {
      assert.equal(await runCourier(), "failed");
      const state = await getState();
      assert.equal(state.lastMakerworldHash, null);
      assert.match(state.lastCourierStatus, /db down/);
    },
  );
});

test("runCourier: disabled or unconfigured does nothing", async () => {
  const h = install();
  try {
    assert.equal(await runCourier(), "disabled");
    await setConfig({ appBaseUrl: "https://a", apiToken: "t", autoCourier: false });
    assert.equal(await runCourier(), "disabled");
  } finally {
    h.uninstall();
  }
});
