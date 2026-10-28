import { test } from "node:test";
import assert from "node:assert/strict";

import { importView, popupView } from "../src/popupLogic.js";
import { describePrinterKey, describeMe, settingsTokensUrl, versionMismatch } from "../src/optionsLogic.js";
import { queueModel, resolvePrinter } from "../src/printQueue.js";

test("popupView: setup / model / collections / detail / other (non-model page, no tab)", () => {
  assert.equal(popupView({ configured: false, url: "https://makerworld.com/en/models/1-a" }), "setup");
  assert.equal(popupView({ configured: true, url: "https://makerworld.com/en/models/1-a" }), "model");
  assert.equal(popupView({ configured: true, url: "https://makerworld.com/en/@me/collections" }), "collections");
  assert.equal(popupView({ configured: true, url: "https://makerworld.com/en/collections/18925823-esp32" }), "collection-detail");
  assert.equal(popupView({ configured: true, url: "https://example.com/" }), "other");
  assert.equal(popupView({ configured: true, url: undefined }), "other");
  assert.equal(popupView({ configured: true, url: "https://makerworld.com/en/@me/collections", hasTab: false }), "other");
});

test("importView: progress wording per state, done links to /models/{id}, failure shows the error", () => {
  const base = "https://app.test/";
  assert.equal(importView({ state: "fetching" }, { base }).text, "Fetching model details…");
  assert.equal(importView({ state: "downloading" }, { base }).text, "Downloading files…");
  assert.equal(importView({ state: "pending" }, { base }).kind, "progress");
  const done = importView({ state: "done", modelId: 42 }, { base });
  assert.deepEqual([done.text, done.done, done.openUrl], ["Imported ✓", true, "https://app.test/models/42"]);
  assert.equal(importView({ state: "done", modelId: 42, deduped: true }, { base }).text, "Already in your library ✓");
  assert.equal(importView({ state: "done", modelId: null }, { base }).openUrl, null);
  const failed = importView({ state: "failed", error: "Bambu sign-in expired" }, { base });
  assert.deepEqual([failed.kind, failed.text], ["error", "Bambu sign-in expired"]);
  assert.equal(importView({ state: "downloading", gaveUp: true }, { base }).kind, "warn");
});

test("importView: queue outcomes are explained (none / multiple / queued / error)", () => {
  const rec = (queueResult) => importView({ state: "done", modelId: 1, queueResult }, { base: "http://a" });
  assert.match(rec({ status: "none" }).queueText, /No sliced file yet — slice it in the app/);
  assert.match(rec({ status: "multiple", count: 3 }).queueText, /3 sliced files/);
  assert.equal(rec({ status: "queued" }).queueKind, "ok");
  assert.equal(rec({ status: "error", message: "boom" }).queueText, "boom");
  assert.equal(rec(null).queueText, null);
});

test("versionMismatch: ignores a leading v and unknown versions; flags a real difference", () => {
  assert.equal(versionMismatch("v0.8.0", "0.8.0"), null);
  assert.equal(versionMismatch(null, "0.8.0"), null);
  assert.match(versionMismatch("0.9.0", "0.8.0"), /app is v0\.9\.0 but this extension is v0\.8\.0/);
});

test("describeMe: a version mismatch is surfaced as a notice and downgrades ok to warn", () => {
  const me = { ok: true, status: 200, data: { username: "a", perms: ["library:write", "settings:write"], app_version: "0.9.0" }, error: null };
  const mismatch = describeMe(me, "0.8.0");
  assert.equal(mismatch.kind, "warn");
  assert.equal(mismatch.notices.length, 1);
  assert.deepEqual(describeMe(me, "0.9.0").notices, []);
});

test("describePrinterKey: needs both queue and read scopes", () => {
  const ping = (scopes) => ({ ok: true, status: 200, data: { ok: true, key: { label: "ext", scopes } }, error: null });
  assert.deepEqual(describePrinterKey(ping(["queue", "read"])), { ok: true, text: 'Printer key "ext" OK.' });
  assert.match(describePrinterKey(ping(["read"])).text, /missing the queue scope\b/);
  assert.match(describePrinterKey(ping([])).text, /queue and read scopes/);
  assert.equal(describePrinterKey({ ok: false, status: 401, data: null, error: "Token rejected" }).ok, false);
});

test("settingsTokensUrl: deep-links to Settings -> Accounts, trimming trailing slashes", () => {
  assert.equal(settingsTokensUrl("https://app.test//"), "https://app.test/settings?tab=accounts");
});

test("resolvePrinter / queueModel: single printer auto-picked, several need a choice", async () => {
  assert.equal(resolvePrinter(4, [{ id: 1 }, { id: 2 }]), 4);
  assert.equal(resolvePrinter(null, [{ id: 1 }]), 1);
  assert.equal(resolvePrinter(null, []), null);
  assert.equal(resolvePrinter(null, [{ id: 1 }, { id: 2 }]), undefined);
  const ok = (data) => ({ ok: true, status: 200, data, error: null });
  const file = [{ file_id: 9, plate: null }];
  const api = (printers, printables = file) => ({
    v2Printables: async () => ok(printables),
    v2Printers: async () => ok(printers),
    v2Enqueue: async () => ok({}),
  });
  assert.deepEqual(await queueModel({ api: api([{ id: 1 }, { id: 2 }]), modelId: 1 }), { status: "need-printer" });
  assert.deepEqual(await queueModel({ api: api([{ id: 1 }, { id: 2 }]), modelId: 1, printerId: 2 }), { status: "queued" });
  assert.deepEqual(await queueModel({ api: api([], [...file, ...file]), modelId: 1 }), { status: "multiple", count: 2 });
});
