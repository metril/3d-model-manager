import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { hashResetPatch, setConfig, getConfig } from "../src/config.js";
import { GALLERY_HOST_PATTERNS } from "../src/detect.js";

/** Installs an in-memory `chrome.storage.local` whose reads/writes yield, to expose races. */
function installChrome() {
  let store = {};
  globalThis.chrome = {
    storage: {
      local: {
        get: async (key) => {
          const snapshot = { [key]: store[key] };
          await new Promise((r) => setTimeout(r, 5));
          return snapshot;
        },
        set: async (obj) => {
          await new Promise((r) => setTimeout(r, 1));
          store = { ...store, ...obj };
        },
      },
    },
  };
}

test("setConfig: concurrent patches are serialized, none are lost", async () => {
  installChrome();
  try {
    await Promise.all([
      setConfig({ appBaseUrl: "https://a.example" }),
      setConfig({ lastMakerworldHash: "h1" }),
      setConfig({ lastCollectionsHash: "h2" }),
    ]);
    const cfg = await getConfig();
    assert.equal(cfg.appBaseUrl, "https://a.example");
    assert.equal(cfg.lastMakerworldHash, "h1");
    assert.equal(cfg.lastCollectionsHash, "h2");
  } finally {
    delete globalThis.chrome;
  }
});

test("setConfig: a failed write does not block later writes", async () => {
  installChrome();
  try {
    const realSet = globalThis.chrome.storage.local.set;
    globalThis.chrome.storage.local.set = async () => {
      throw new Error("quota");
    };
    await assert.rejects(setConfig({ apiToken: "x" }), /quota/);
    globalThis.chrome.storage.local.set = realSet;
    const next = await setConfig({ apiToken: "y" });
    assert.equal(next.apiToken, "y");
  } finally {
    delete globalThis.chrome;
  }
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
