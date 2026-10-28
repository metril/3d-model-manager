import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { createClient } from "../src/api.js";

/** Installs a stub `fetch` for the duration of `fn`, then restores it. */
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

test("ping: hits GET <base>/api/ext/ping with the bearer header, no body", async () => {
  await withStubFetch(
    () => jsonResponse(200, { ok: true }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080/", token: "tok123" });
      const result = await client.ping();

      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/ping");
      assert.equal(calls[0].init.method, "GET");
      assert.equal(calls[0].init.headers.Authorization, "Bearer tok123");
      assert.equal(calls[0].init.body, undefined);
      assert.deepEqual(result, { ok: true, status: 200, data: { ok: true }, error: null });
    }
  );
});

test("baseUrl: a trailing slash is trimmed before building the request URL", async () => {
  await withStubFetch(
    () => jsonResponse(200, { ok: true }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080///", token: "t" });
      await client.ping();
      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/ping");
    }
  );
});

test("createImport: POSTs the url as JSON with Content-Type", async () => {
  await withStubFetch(
    () => jsonResponse(201, { id: "abc", state: "queued", site: "makerworld" }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const result = await client.createImport("https://makerworld.com/en/models/1-foo");

      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/imports");
      assert.equal(calls[0].init.method, "POST");
      assert.equal(calls[0].init.headers["Content-Type"], "application/json");
      assert.deepEqual(JSON.parse(calls[0].init.body), {
        url: "https://makerworld.com/en/models/1-foo",
      });
      assert.equal(result.ok, true);
      assert.equal(result.status, 201);
    }
  );
});

test("getImportStatus: GETs /imports/<id> with the bearer header, no body", async () => {
  await withStubFetch(
    () => jsonResponse(200, { id: 42, state: "done", error: null }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const result = await client.getImportStatus(42);

      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/imports/42");
      assert.equal(calls[0].init.method, "GET");
      assert.equal(calls[0].init.headers.Authorization, "Bearer tok123");
      assert.equal(calls[0].init.body, undefined);
      assert.deepEqual(result, {
        ok: true,
        status: 200,
        data: { id: 42, state: "done", error: null },
        error: null,
      });
    }
  );
});

test("getImportStatus: URL-encodes the importId path segment", async () => {
  await withStubFetch(
    () => jsonResponse(200, { id: 1, state: "done", error: null }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      await client.getImportStatus("weird id");

      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/imports/weird%20id");
    }
  );
});

test("getImportStatus: a 404 surfaces as a non-ok result", async () => {
  await withStubFetch(
    () => jsonResponse(404, { detail: "import 999 not found" }),
    async () => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const result = await client.getImportStatus(999);

      assert.equal(result.ok, false);
      assert.equal(result.status, 404);
      assert.equal(result.error, "import 999 not found");
    }
  );
});

test("setMakerworldCredential: POSTs the cookie value as {token}", async () => {
  await withStubFetch(
    () => jsonResponse(200, { ok: true }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      await client.setMakerworldCredential("cookie-value");

      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/credentials/makerworld");
      assert.deepEqual(JSON.parse(calls[0].init.body), { token: "cookie-value" });
    }
  );
});

test("pushCollections: POSTs {site, collections} to /collections", async () => {
  await withStubFetch(
    () => jsonResponse(200, { ok: true, count: 2 }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const collections = [
        { list_id: "1", title: "Default Collection", slug: null, count: 7, is_default: true },
        { list_id: "2", title: "ESP32", slug: "esp32", count: 9, is_default: false },
      ];
      const result = await client.pushCollections("makerworld", collections);

      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/collections");
      assert.equal(calls[0].init.method, "POST");
      assert.equal(calls[0].init.headers["Content-Type"], "application/json");
      assert.deepEqual(JSON.parse(calls[0].init.body), { site: "makerworld", collections });
      assert.deepEqual(result, { ok: true, status: 200, data: { ok: true, count: 2 }, error: null });
    }
  );
});

test("pushCollectionItems: POSTs {site, items} to /collections/<listId>/items", async () => {
  await withStubFetch(
    () => jsonResponse(200, { ok: true, count: 2 }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const items = [
        {
          external_id: "111",
          title: "ESP32 case",
          url: "https://makerworld.com/en/models/111",
          author: "someone",
          thumbnail_url: "https://makerworld.bblmw.com/cover1.jpg",
        },
        {
          external_id: "222",
          title: "ESP32 mount",
          url: "https://makerworld.com/en/models/222",
          author: null,
          thumbnail_url: null,
        },
      ];
      const result = await client.pushCollectionItems("makerworld", "18925823", items);

      assert.equal(calls[0].url, "http://nas.local:8080/api/ext/collections/18925823/items");
      assert.equal(calls[0].init.method, "POST");
      assert.equal(calls[0].init.headers["Content-Type"], "application/json");
      assert.deepEqual(JSON.parse(calls[0].init.body), { site: "makerworld", items });
      assert.deepEqual(result, { ok: true, status: 200, data: { ok: true, count: 2 }, error: null });
    }
  );
});

test("pushCollectionItems: URL-encodes the listId path segment", async () => {
  await withStubFetch(
    () => jsonResponse(200, { ok: true, count: 0 }),
    async (calls) => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      await client.pushCollectionItems("makerworld", "weird/id with space", []);

      assert.equal(
        calls[0].url,
        "http://nas.local:8080/api/ext/collections/weird%2Fid%20with%20space/items"
      );
    }
  );
});

test("pushCollections: non-2xx surfaces the response's `detail` as `error`", async () => {
  await withStubFetch(
    () => jsonResponse(422, { detail: "collections: at most 200 entries" }),
    async () => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const result = await client.pushCollections("makerworld", []);

      assert.equal(result.ok, false);
      assert.equal(result.status, 422);
      assert.equal(result.error, "Invalid request (422): collections: at most 200 entries");
    }
  );
});

test("non-2xx: surfaces the response's `detail` as `error`", async () => {
  await withStubFetch(
    () => jsonResponse(422, { detail: "Unsupported or invalid URL" }),
    async () => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const result = await client.createImport("https://example.com/not-a-model");

      assert.equal(result.ok, false);
      assert.equal(result.status, 422);
      assert.equal(result.error, "Invalid request (422): Unsupported or invalid URL");
    }
  );
});

test("non-2xx: joins an array `detail` (FastAPI validation shape) instead of stringifying it", async () => {
  await withStubFetch(
    () =>
      jsonResponse(422, {
        detail: [
          { loc: ["body", "url"], msg: "field required", type: "value_error.missing" },
          { loc: ["body", "token"], msg: "field required", type: "value_error.missing" },
        ],
      }),
    async () => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123" });
      const result = await client.createImport("https://example.com/not-a-model");

      assert.equal(result.ok, false);
      assert.equal(result.status, 422);
      assert.equal(result.error, "Invalid request (422): field required; field required");
      assert.doesNotMatch(result.error, /\[object Object\]/);
    }
  );
});

test("401: surfaces as a non-ok result without throwing", async () => {
  await withStubFetch(
    () => jsonResponse(401, { detail: "Not authenticated" }),
    async () => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "bad" });
      const result = await client.ping();

      assert.equal(result.ok, false);
      assert.equal(result.status, 401);
      assert.equal(result.error, "Token rejected (401) — check your API token. Not authenticated");
    }
  );
});

test("network failure: resolves to a normalized error instead of throwing", async () => {
  await withStubFetch(
    () => {
      throw new Error("getaddrinfo ENOTFOUND nas.local");
    },
    async () => {
      const client = createClient({ baseUrl: "http://nas.local:8080", token: "tok123", retryDelayMs: 0 });
      const result = await client.ping();

      assert.equal(result.ok, false);
      assert.equal(result.status, 0);
      assert.match(result.error, /ENOTFOUND/);
    }
  );
});

test("contract: every api.js call's method + path appears in test/contract.json", async () => {
  const contract = JSON.parse(readFileSync(new URL("./contract.json", import.meta.url), "utf8"));
  const matchers = contract.map((e) => ({
    method: e.method,
    re: new RegExp(`^${e.path.replace(/\{[^}]+\}/g, "[^/]+")}$`),
  }));
  // One representative call per client method; a new method without an entry fails below.
  const calls = {
    ping: (c) => c.ping(),
    me: (c) => c.me(),
    createImport: (c) => c.createImport("u", { collectionId: 1, tags: ["a"] }),
    getImportStatus: (c) => c.getImportStatus(5),
    setMakerworldCredential: (c) => c.setMakerworldCredential("v"),
    pushCollections: (c) => c.pushCollections("makerworld", []),
    upsertCollection: (c) => c.upsertCollection("makerworld", { list_id: "9", title: "t", slug: null, count: 1, is_default: false }),
    pushCollectionItems: (c) => c.pushCollectionItems("makerworld", "9", []),
    listLocalCollections: (c) => c.listLocalCollections(),
    listTags: (c) => c.listTags(),
    v2Ping: (c) => c.v2Ping(),
    v2Printers: (c) => c.v2Printers(),
    v2Printables: (c) => c.v2Printables(3),
    v2Enqueue: (c) => c.v2Enqueue({ modelId: 3, fileId: 4, plate: 1, printerId: 2 }),
  };
  await withStubFetch(
    () => jsonResponse(200, { ok: true }),
    async (seen) => {
      const client = createClient({ baseUrl: "http://a", token: "t", retryDelayMs: 0 });
      assert.deepEqual(Object.keys(client).sort(), Object.keys(calls).sort(), "client method without a contract call");
      for (const [name, run] of Object.entries(calls)) {
        seen.length = 0;
        await run(client);
        const { url, init } = seen[0];
        const path = new URL(url).pathname.replace(/^\/api/, "");
        assert.ok(
          matchers.some((m) => m.method === init.method && m.re.test(path)),
          `${name}: ${init.method} ${path} is not in contract.json`,
        );
      }
    },
  );
});
