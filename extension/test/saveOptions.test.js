import { test } from "node:test";
import assert from "node:assert/strict";

import { install } from "./chromeStub.js";
import { createClient } from "../src/api.js";
import { buildSaveOptions, clearCaches, loadOptionLists, normalizeTags, splitTagInput } from "../src/saveOptions.js";

test("normalizeTags: trims, dedupes case-insensitively, drops empty/over-long, caps at 20", () => {
  assert.deepEqual(normalizeTags([" Benchy ", "benchy", "", "  ", "x".repeat(65), "Tool"]), ["Benchy", "Tool"]);
  assert.equal(normalizeTags(Array.from({ length: 30 }, (_, i) => `t${i}`)).length, 20);
  assert.deepEqual(normalizeTags("nope"), []);
  assert.deepEqual(splitTagInput("a, b\nc"), ["a", " b", "c"]);
});

test("buildSaveOptions: empty collection -> null, numeric string -> int", () => {
  assert.deepEqual(buildSaveOptions({ collectionId: "", tags: ["a"] }), { collectionId: null, tags: ["a"] });
  assert.deepEqual(buildSaveOptions({ collectionId: "12", tags: [] }), { collectionId: 12, tags: [] });
  assert.deepEqual(buildSaveOptions({ collectionId: "abc", tags: [] }), { collectionId: null, tags: [] });
});

test("createImport payload: collection_id/tags only when set (so defaults match the old body)", async () => {
  const bodies = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return { ok: true, status: 201, statusText: "", json: async () => ({ id: 1 }) };
  };
  try {
    const client = createClient({ baseUrl: "http://a", token: "t" });
    await client.createImport("u1");
    await client.createImport("u2", { collectionId: 3, tags: ["x", "y"] });
    await client.createImport("u3", { collectionId: 0, tags: [] });
    assert.deepEqual(bodies, [
      { url: "u1" },
      { url: "u2", collection_id: 3, tags: ["x", "y"] },
      { url: "u3", collection_id: 0 },
    ]);
  } finally {
    globalThis.fetch = original;
  }
});

test("loadOptionLists: fetches once, then serves the session cache until it expires", async () => {
  const stub = install();
  try {
    let calls = 0;
    const api = {
      listLocalCollections: async () => (calls++, { ok: true, data: [{ id: 1, name: "Boxes" }, { id: "x" }] }),
      listTags: async () => ({ ok: true, data: [{ name: "benchy" }] }),
    };
    let t = 0;
    const first = await loadOptionLists(api, { now: () => t });
    assert.deepEqual(first, { collections: [{ id: 1, name: "Boxes" }], tags: ["benchy"] });
    await loadOptionLists(api, { now: () => (t = 1000) });
    assert.equal(calls, 1);
    await loadOptionLists(api, { now: () => (t = 10 * 60 * 1000) });
    assert.equal(calls, 2);
  } finally {
    stub.uninstall();
  }
});

test("loadOptionLists: a failed half yields an empty list and is not cached", async () => {
  const stub = install();
  try {
    let calls = 0;
    const api = {
      listLocalCollections: async () => (calls++, { ok: false, status: 500, data: null, error: "down" }),
      listTags: async () => ({ ok: true, data: [{ name: "a" }] }),
    };
    assert.deepEqual(await loadOptionLists(api), { collections: [], tags: ["a"] });
    await loadOptionLists(api);
    assert.equal(calls, 2);
  } finally {
    stub.uninstall();
  }
});

test("option lists are cached per app URL and clearCaches drops every cache key", async () => {
  const stub = install();
  try {
    let calls = 0;
    const api = {
      listLocalCollections: async () => (calls++, { ok: true, data: [{ id: 1, name: "A" }] }),
      listTags: async () => ({ ok: true, data: [{ name: "t" }] }),
    };
    await loadOptionLists(api, { baseUrl: "http://a" });
    await loadOptionLists(api, { baseUrl: "http://a" });
    assert.equal(calls, 1);
    await loadOptionLists(api, { baseUrl: "http://b" }); // other server: no cross-talk
    assert.equal(calls, 2);
    await chrome.storage.session.set({ "me-cache:http://a": { username: "x" }, other: 1 });
    await clearCaches();
    assert.deepEqual(Object.keys(await chrome.storage.session.get(null)), ["other"]);
  } finally {
    stub.uninstall();
  }
});
