import { test } from "node:test";
import assert from "node:assert/strict";

import { createClient } from "../src/api.js";

async function withStubFetch(handler, fn) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  try {
    await fn(calls);
  } finally {
    globalThis.fetch = original;
  }
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `Status ${status}`,
    json: async () => body,
  };
}

const mk = () => createClient({ baseUrl: "http://nas.local:8080", token: "t", retryDelayMs: 0 });

test("requests use redirect:error and omit credentials", async () => {
  await withStubFetch(
    () => jsonResponse(200, {}),
    async (calls) => {
      await mk().ping();
      assert.equal(calls[0].init.redirect, "error");
      assert.equal(calls[0].init.credentials, "omit");
    },
  );
});

test("error mapping: 403, 5xx include status; object detail is JSON-stringified", async () => {
  await withStubFetch(
    () => jsonResponse(403, { detail: "missing scope" }),
    async () => {
      const r = await mk().ping();
      assert.equal(r.error, "Token lacks permission (403) — missing scope");
    },
  );
  await withStubFetch(
    () => jsonResponse(500, { detail: { code: "boom" } }),
    async () => {
      const r = await mk().ping();
      assert.equal(r.error, 'Server error (500): {"code":"boom"}');
    },
  );
});

test("timeout maps to a friendly message with status 0", async () => {
  await withStubFetch(
    () => {
      const e = new Error("The operation timed out");
      e.name = "TimeoutError";
      throw e;
    },
    async () => {
      const r = await mk().ping();
      assert.equal(r.status, 0);
      assert.match(r.error, /timed out/);
    },
  );
});

test("GET retries once on 5xx and succeeds", async () => {
  let n = 0;
  await withStubFetch(
    () => (++n === 1 ? jsonResponse(503, { detail: "busy" }) : jsonResponse(200, { ok: true })),
    async (calls) => {
      const r = await mk().ping();
      assert.equal(r.ok, true);
      assert.equal(calls.length, 2);
    },
  );
});

test("no retry on 4xx, and createImport (non-idempotent) is never retried", async () => {
  await withStubFetch(
    () => jsonResponse(404, { detail: "nope" }),
    async (calls) => {
      await mk().ping();
      assert.equal(calls.length, 1);
    },
  );
  await withStubFetch(
    () => jsonResponse(502, { detail: "bad gateway" }),
    async (calls) => {
      const r = await mk().createImport("https://makerworld.com/en/models/1");
      assert.equal(r.status, 502);
      assert.equal(calls.length, 1);
    },
  );
});

test("upsertCollection: PUTs {site, title, slug, count, is_default} to /collections/<id>", async () => {
  await withStubFetch(
    () => jsonResponse(200, { ok: true }),
    async (calls) => {
      const entry = { list_id: "18/9", title: "ESP32", slug: "esp32", count: 3, is_default: false };
      await mk().upsertCollection("makerworld", entry);
      assert.equal(calls[0].init.method, "PUT");
      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/collections/18%2F9");
      assert.deepEqual(JSON.parse(calls[0].init.body), {
        site: "makerworld",
        title: "ESP32",
        slug: "esp32",
        count: 3,
        is_default: false,
      });
    },
  );
});

test("me: returns /ext/me data; falls back to /ping on 404 (older server)", async () => {
  await withStubFetch(
    () => jsonResponse(200, { username: "alice", perms: ["library:write"], app_version: "0.8.0" }),
    async (calls) => {
      const r = await mk().me();
      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/me");
      assert.equal(r.data.username, "alice");
    },
  );
  await withStubFetch(
    (url) => (url.endsWith("/me") ? jsonResponse(404, { detail: "Not Found" }) : jsonResponse(200, { ok: true })),
    async (calls) => {
      const r = await mk().me();
      assert.equal(r.ok, true);
      assert.equal(r.data.legacy, true);
      assert.equal(calls.length, 2);
    },
  );
});
