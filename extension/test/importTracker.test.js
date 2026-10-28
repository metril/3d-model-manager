import { test } from "node:test";
import assert from "node:assert/strict";

import { install } from "./chromeStub.js";
import {
  findImportForUrl,
  listActiveImports,
  patchImport,
  readImport,
  runQueue,
  startSave,
  trackImport,
} from "../src/importTracker.js";

const URL = "https://makerworld.com/en/models/1-foo";
const ok = (data, status = 200) => ({ ok: true, status, data, error: null });

/** Fake clock: `sleep` advances `now`, so deadline polling runs instantly. */
function clock() {
  let t = 1_000_000;
  return { now: () => t, sleep: async (ms) => void (t += ms) };
}

/** Deps with a scripted status sequence (last entry repeats). */
function makeDeps({ statuses, v2Api = null, lastSave = {} } = {}) {
  const c = clock();
  const badges = [];
  let i = 0;
  return {
    badges,
    deps: {
      api: {
        getImportStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
        createImport: async (url, options) => ok({ id: 7, state: "pending", options }, 201),
      },
      v2Api,
      setBadge: async (tabId, text, color) => void badges.push([tabId, text, color]),
      getState: async () => ({ lastSave: { collectionId: null, tags: [], printerId: null, ...lastSave } }),
      ...c,
      intervalMs: 1000,
      timeoutMs: 60_000,
    },
  };
}

async function seed(id, extra = {}) {
  await chrome.storage.session.set({
    [`imp:${id}`]: { id, url: URL, tabId: 3, state: "pending", modelId: null, startedAt: 1_000_000, finished: false, queue: null, queueResult: null, ...extra },
  });
}

test("trackImport lifecycle: pending -> fetching -> done mirrors state and badges the tab", async () => {
  const stub = install();
  try {
    await seed(7);
    const seen = [];
    stub.chrome.storage.onChanged.addListener((changes) => {
      if (changes["imp:7"]) seen.push(changes["imp:7"].newValue.state);
    });
    const { deps, badges } = makeDeps({
      statuses: [ok({ id: 7, state: "pending" }), ok({ id: 7, state: "fetching" }), ok({ id: 7, state: "done", model_id: 42 })],
    });
    const rec = await trackImport(7, deps);
    assert.equal(rec.state, "done");
    assert.equal(rec.finished, true);
    assert.equal(rec.modelId, 42);
    assert.deepEqual(seen.slice(0, 3), ["pending", "fetching", "done"]);
    assert.deepEqual(badges[0], [3, "…", "#475569"]);
    assert.deepEqual(badges.at(-1), [3, "✓", "#2e7d32"]);
  } finally {
    stub.uninstall();
  }
});

test("trackImport: a failed import records the error and badges '!'", async () => {
  const stub = install();
  try {
    await seed(7);
    const { deps, badges } = makeDeps({ statuses: [ok({ id: 7, state: "failed", error: "Bambu sign-in expired" })] });
    const rec = await trackImport(7, deps);
    assert.equal(rec.state, "failed");
    assert.equal(rec.error, "Bambu sign-in expired");
    assert.deepEqual(badges.at(-1), [3, "!", "#b3261e"]);
    assert.deepEqual(await listActiveImports(), []);
  } finally {
    stub.uninstall();
  }
});

test("trackImport: a poll that times out leaves the record unfinished for the alarm to resume", async () => {
  const stub = install();
  try {
    await seed(7);
    const { deps } = makeDeps({ statuses: [ok({ id: 7, state: "downloading" })] });
    const rec = await trackImport(7, deps);
    assert.equal(rec.finished, false);
    assert.equal(rec.state, "downloading");
    assert.equal((await listActiveImports()).length, 1);
    // Far past the give-up age: the next resume closes it out.
    await patchImport(7, { startedAt: -3_000_000 });
    const closed = await trackImport(7, deps);
    assert.equal(closed.finished, true);
    assert.equal(closed.gaveUp, true);
  } finally {
    stub.uninstall();
  }
});

test("trackImport with queue: queues once done when exactly one sliced file exists", async () => {
  const stub = install();
  try {
    await seed(7, { queue: { printerId: null } });
    const enqueued = [];
    const v2Api = {
      v2Printables: async () => ok([{ file_id: 9, name: "a.gcode.3mf", kind: "sliced", plate: null }]),
      v2Printers: async () => ok([{ id: 5, name: "A1" }]),
      v2Enqueue: async (body) => (enqueued.push(body), ok({}, 201)),
    };
    const { deps, badges } = makeDeps({ statuses: [ok({ id: 7, state: "done", model_id: 42 })], v2Api });
    const rec = await trackImport(7, deps);
    assert.deepEqual(rec.queueResult, { status: "queued" });
    assert.deepEqual(enqueued, [{ modelId: 42, fileId: 9, plate: null, printerId: 5 }]);
    assert.deepEqual(badges.at(-1), [3, "✓", "#2e7d32"]);
  } finally {
    stub.uninstall();
  }
});

test("trackImport with queue: no sliced file -> '!' badge and a 'none' result the popup can explain", async () => {
  const stub = install();
  try {
    await seed(7, { queue: { printerId: null } });
    const v2Api = { v2Printables: async () => ok([]) };
    const { deps, badges } = makeDeps({ statuses: [ok({ id: 7, state: "done", model_id: 42 })], v2Api });
    const rec = await trackImport(7, deps);
    assert.deepEqual(rec.queueResult, { status: "none" });
    assert.deepEqual(badges.at(-1), [3, "!", "#b3261e"]);
  } finally {
    stub.uninstall();
  }
});

test("runQueue: uses the remembered printer; errors are reported, never thrown", async () => {
  const stub = install();
  try {
    await seed(7, { state: "done", finished: true, modelId: 42 });
    const enqueued = [];
    const v2Api = {
      v2Printables: async () => ok([{ file_id: 9, plate: 2 }]),
      v2Enqueue: async (body) => (enqueued.push(body), { ok: false, status: 403, data: null, error: "Token lacks permission (403)" }),
    };
    const { deps } = makeDeps({ statuses: [], v2Api, lastSave: { printerId: 8 } });
    const rec = await runQueue(7, deps);
    assert.equal(enqueued[0].printerId, 8);
    assert.equal(enqueued[0].plate, 2);
    assert.deepEqual(rec.queueResult, { status: "error", message: "Token lacks permission (403)" });
  } finally {
    stub.uninstall();
  }
});

test("startSave: forwards save options, stores the record + url index, skips failures", async () => {
  const stub = install();
  try {
    const { deps } = makeDeps({ statuses: [] });
    const res = await startSave({ url: URL, tabId: 3, options: { collectionId: 4, tags: ["a"] }, queue: { printerId: 5 } }, deps);
    assert.equal(res.importId, 7);
    assert.deepEqual(res.data.options, { collectionId: 4, tags: ["a"] });
    const rec = await readImport(7);
    assert.equal(rec.deduped, false);
    assert.deepEqual(rec.queue, { printerId: 5 });
    assert.equal((await findImportForUrl(URL)).id, 7);

    deps.api.createImport = async () => ({ ok: false, status: 422, data: null, error: "bad" });
    const failed = await startSave({ url: "https://x", tabId: 3 }, deps);
    assert.equal(failed.ok, false);
    assert.equal(failed.importId, undefined);
    assert.equal(await findImportForUrl("https://x"), null);
  } finally {
    stub.uninstall();
  }
});
