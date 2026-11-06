import { test } from "node:test";
import assert from "node:assert/strict";

import { install } from "./chromeStub.js";
import { getConfig, getState, setConfig, setState } from "../src/config.js";
import { execInTab, topDocumentId } from "../src/exec.js";
import { staleOriginPattern } from "../src/optionsLogic.js";
import {
  FINISHED_TTL_MS,
  patchImport,
  pruneFinishedImports,
  readImport,
  runQueue,
  trackImport,
} from "../src/importTracker.js";

test("setConfig writes only the config key; setState only the state key", async () => {
  const h = install();
  try {
    await setConfig({ appBaseUrl: "https://a.example" });
    await setState({ lastMakerworldHash: "h1" });
    const writes = [];
    const orig = chrome.storage.local.set.bind(chrome.storage.local);
    chrome.storage.local.set = async (items) => {
      writes.push(Object.keys(items));
      return orig(items);
    };
    await setConfig({ apiToken: "t" });
    await setState({ lastCollectionsHash: "h2" });
    assert.deepEqual(writes.map((k) => k.length), [1, 1]);
    assert.notDeepEqual(writes[0], writes[1]);
    assert.equal((await getConfig()).apiToken, "t");
    assert.equal((await getState()).lastMakerworldHash, "h1");
  } finally {
    h.uninstall();
  }
});

test("pruneFinishedImports drops only old finished records and their url index", async () => {
  const h = install();
  try {
    const t0 = 5_000_000;
    const base = { url: "https://makerworld.com/x", state: "done", queue: null, queueResult: null };
    await chrome.storage.session.set({
      "imp:1": { ...base, id: 1, finished: true, startedAt: t0 - 2 * FINISHED_TTL_MS, finishedAt: t0 - 2 * FINISHED_TTL_MS },
      "imp:2": { ...base, id: 2, finished: true, startedAt: t0, finishedAt: t0 - 1000 },
      "imp:3": { ...base, id: 3, finished: false, startedAt: t0 - 5 * FINISHED_TTL_MS },
      "imp-url:https://makerworld.com/x": 1,
    });
    assert.equal(await pruneFinishedImports(() => t0), 1);
    assert.equal(await readImport(1), null);
    assert.ok(await readImport(2));
    assert.ok(await readImport(3));
    assert.equal((await chrome.storage.session.get("imp-url:https://makerworld.com/x"))["imp-url:https://makerworld.com/x"], undefined);
  } finally {
    h.uninstall();
  }
});

test("a removed printer key yields a queueResult error instead of silence", async () => {
  const h = install();
  try {
    await chrome.storage.session.set({
      "imp:9": { id: 9, url: "u", tabId: 1, state: "done", modelId: 4, startedAt: 1, finished: false, queue: { printerId: 2 }, queueResult: null },
    });
    const deps = { v2Api: null, setBadge: async () => {}, getState: async () => ({ lastSave: {} }) };
    const rec = await runQueue(9, deps);
    assert.equal(rec.queueResult.status, "error");
    assert.match(rec.queueResult.message, /printer key/i);

    await patchImport(9, { queueResult: null, finished: false });
    const done = await trackImport(9, {
      api: { getImportStatus: async () => ({ ok: true, status: 200, data: { state: "done", model_id: 4 } }) },
      ...deps,
      now: () => 10,
      sleep: async () => {},
      intervalMs: 1,
      timeoutMs: 1000,
    });
    assert.equal(done.finished, true);
    assert.equal(done.queueResult.status, "error");
  } finally {
    h.uninstall();
  }
});

test("staleOriginPattern: only a changed origin yields a pattern", () => {
  assert.equal(staleOriginPattern("http://old.lan:8080/app", "http://new.lan:8080"), "http://old.lan:8080/*");
  assert.equal(staleOriginPattern("http://a.lan:8080/x", "http://a.lan:8080/y"), null);
  assert.equal(staleOriginPattern("", "http://a.lan"), null);
  assert.equal(staleOriginPattern("nonsense", "http://a.lan"), null);
});

test("execInTab targets documentIds when given a documentId, else the tab", async () => {
  const h = install();
  try {
    const targets = [];
    chrome.scripting = {
      executeScript: async ({ target }) => {
        targets.push(target);
        return [{ result: 1 }];
      },
    };
    await execInTab(5, () => 1);
    await execInTab({ tabId: 5, documentId: "DOC" }, () => 1);
    assert.deepEqual(targets, [{ tabId: 5 }, { tabId: 5, documentIds: ["DOC"] }]);

    chrome.webNavigation = { getFrame: async () => ({ documentId: "D1" }) };
    assert.equal(await topDocumentId(5), "D1");
    chrome.webNavigation = { getFrame: async () => { throw new Error("no"); } };
    assert.equal(await topDocumentId(5), undefined);
  } finally {
    h.uninstall();
  }
});
