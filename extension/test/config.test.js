import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  SCHEMA_VERSION,
  coerceConfig,
  coerceState,
  getConfig,
  getState,
  hashResetPatch,
  setConfig,
  setState,
} from "../src/config.js";
import { install } from "./chromeStub.js";
import { GALLERY_HOST_PATTERNS } from "../src/detect.js";

test("setConfig/setState: concurrent writers to both keys are serialized, none are lost", async () => {
  const h = install();
  try {
    await Promise.all([
      setConfig({ appBaseUrl: "https://a.example" }),
      setState({ lastMakerworldHash: "h1" }),
      setState({ lastCollectionsHash: "h2" }),
      setConfig({ apiToken: "t" }),
    ]);
    const cfg = await getConfig();
    const st = await getState();
    assert.equal(cfg.appBaseUrl, "https://a.example");
    assert.equal(cfg.apiToken, "t");
    assert.equal(st.lastMakerworldHash, "h1");
    assert.equal(st.lastCollectionsHash, "h2");
    assert.equal(cfg.lastMakerworldHash, undefined); // hashes live in state only
  } finally {
    h.uninstall();
  }
});

test("setState: updater function runs inside the lock (no lost array updates)", async () => {
  const h = install();
  try {
    await Promise.all(
      ["1", "2", "3", "4"].map((id) =>
        setState((cur) => ({
          lastCollectionItemsHash: [...cur.lastCollectionItemsHash, { listId: id, hash: "x" }],
        })),
      ),
    );
    const st = await getState();
    assert.deepEqual(st.lastCollectionItemsHash.map((e) => e.listId).sort(), ["1", "2", "3", "4"]);
  } finally {
    h.uninstall();
  }
});

test("setConfig: a failed write does not block later writes", async () => {
  const h = install();
  try {
    const realSet = h.chrome.storage.local.set;
    h.chrome.storage.local.set = async () => {
      throw new Error("quota");
    };
    await assert.rejects(setConfig({ apiToken: "x" }), /quota/);
    h.chrome.storage.local.set = realSet;
    const next = await setConfig({ apiToken: "y" });
    assert.equal(next.apiToken, "y");
  } finally {
    h.uninstall();
  }
});

test("migration: old single blob splits into config + state, once", async () => {
  const h = install();
  try {
    h.stores.local.config = {
      appBaseUrl: "https://a",
      apiToken: "tok",
      autoCourier: false,
      lastMakerworldHash: "mh",
      lastCollectionsHash: "ch",
      lastCollectionItemsHash: [{ listId: "5", hash: "ih" }],
    };
    const cfg = await getConfig();
    assert.equal(cfg.schemaVersion, SCHEMA_VERSION);
    assert.equal(cfg.autoCourier, false);
    assert.equal(cfg.appBaseUrl, "https://a");
    const st = await getState();
    assert.equal(st.lastMakerworldHash, "mh");
    assert.deepEqual(st.lastCollectionItemsHash, [{ listId: "5", hash: "ih" }]);
    // persisted split: config key no longer carries hashes
    assert.equal("lastMakerworldHash" in h.stores.local.config, false);
    assert.equal(h.stores.local.state.lastCollectionsHash, "ch");
  } finally {
    h.uninstall();
  }
});

test("coerce: wrong-typed stored values fall back to defaults", () => {
  const cfg = coerceConfig({ appBaseUrl: 5, apiToken: null, autoCourier: "yes", autoSyncCollections: 0 });
  assert.deepEqual(cfg, {
    schemaVersion: SCHEMA_VERSION,
    appBaseUrl: "",
    apiToken: "",
    printerKey: "",
    autoCourier: true,
    autoSyncCollections: true,
  });
  const st = coerceState({ lastMakerworldHash: 7, lastCollectionItemsHash: [null, { listId: 1 }, { listId: "a", hash: "b" }] });
  assert.equal(st.lastMakerworldHash, null);
  assert.deepEqual(st.lastCollectionItemsHash, [{ listId: "a", hash: "b" }]);
});

test("hashResetPatch: clears hashes only when URL or token changes", () => {
  const prev = { appBaseUrl: "https://a", apiToken: "t" };
  assert.deepEqual(hashResetPatch(prev, { ...prev }), {});
  const cleared = { lastMakerworldHash: null, lastCollectionsHash: null, lastCollectionItemsHash: [] };
  assert.deepEqual(hashResetPatch(prev, { appBaseUrl: "https://b", apiToken: "t" }), cleared);
  assert.deepEqual(hashResetPatch(prev, { appBaseUrl: "https://a", apiToken: "u" }), cleared);
});

test("GALLERY_HOST_PATTERNS equals manifest host_permissions (all https)", () => {
  const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
  assert.deepEqual([...GALLERY_HOST_PATTERNS].sort(), [...manifest.host_permissions].sort());
  assert.ok(GALLERY_HOST_PATTERNS.every((p) => p.startsWith("https://")));
  assert.ok(!manifest.permissions.includes("activeTab"));
});
