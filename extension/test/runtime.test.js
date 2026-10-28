import { test } from "node:test";
import assert from "node:assert/strict";

import { appOriginPattern, grantAccess, missingOrigins, renderGrantAccess, requiredOrigins } from "../src/access.js";
import { execInTab } from "../src/exec.js";
import { isOwnExtensionSender } from "../src/guards.js";
import { describeMe, isPrivateHost, normalizeBaseUrl } from "../src/optionsLogic.js";
import { isInFlight, runOnce } from "../src/runOnce.js";
import { install } from "./chromeStub.js";

// --- runOnce ---------------------------------------------------------------

test("runOnce: a trigger during a run sets dirty and re-runs exactly once", async () => {
  let runs = 0;
  let release;
  const gate = new Promise((r) => (release = r));
  const first = runOnce("k1", async () => {
    runs++;
    if (runs === 1) await gate;
  });
  await runOnce("k1", async () => assert.fail("must not run concurrently"));
  await runOnce("k1", async () => assert.fail("must not run concurrently")); // coalesced
  release();
  await first;
  assert.equal(runs, 2);
  assert.equal(isInFlight("k1"), false);
});

test("runOnce: the key is released even when fn throws", async () => {
  await assert.rejects(
    runOnce("k2", async () => {
      throw new Error("boom");
    }),
    /boom/,
  );
  assert.equal(isInFlight("k2"), false);
  let ran = false;
  await runOnce("k2", async () => {
    ran = true;
  });
  assert.equal(ran, true);
});

// --- execInTab -------------------------------------------------------------

test("execInTab: returns results[0].result", async () => {
  const h = install({ scripting: { executeScript: async () => [{ result: { ok: 1 } }] } });
  try {
    assert.deepEqual(await execInTab(1, () => {}, []), { ok: 1 });
  } finally {
    h.uninstall();
  }
});

test("execInTab: a hung injection rejects after the timeout (so inFlight keys release)", async () => {
  const h = install({ scripting: { executeScript: () => new Promise(() => {}) } });
  try {
    await assert.rejects(execInTab(1, () => {}, [], 20), /Timed out/);
  } finally {
    h.uninstall();
  }
});

// --- guards ----------------------------------------------------------------

test("isOwnExtensionSender: needs our id AND an extension-origin url", () => {
  const runtime = { id: "me", getURL: (p) => `chrome-extension://me/${p}` };
  assert.equal(isOwnExtensionSender({ id: "me", url: "chrome-extension://me/src/popup.html" }, runtime), true);
  assert.equal(isOwnExtensionSender({ id: "me", url: "https://evil.example/page" }, runtime), false);
  assert.equal(isOwnExtensionSender({ id: "me" }, runtime), false);
  assert.equal(isOwnExtensionSender({ id: "other", url: "chrome-extension://me/x" }, runtime), false);
  assert.equal(isOwnExtensionSender(undefined, runtime), false);
});

// --- access ----------------------------------------------------------------

test("requiredOrigins / appOriginPattern", () => {
  assert.equal(appOriginPattern("https://nas.local:8080/some/path"), "https://nas.local:8080/*");
  assert.equal(appOriginPattern("nope"), null);
  const all = requiredOrigins("http://nas.local:8080");
  assert.equal(all[0], "http://nas.local:8080/*");
  assert.ok(all.includes("https://makerworld.com/*"));
  assert.equal(requiredOrigins("").length, 6);
});

test("missingOrigins/grantAccess: asks only for what is missing, in one request", async () => {
  const h = install();
  try {
    h.chrome.__granted = ({ origins }) => origins[0].includes("makerworld.com"); // gallery granted, app not
    assert.deepEqual(await missingOrigins("http://nas.local:8080"), ["http://nas.local:8080/*"].concat(
      requiredOrigins("x").filter((o) => !o.includes("makerworld.com")),
    ));
    assert.equal(await grantAccess("http://nas.local:8080"), true);
    assert.equal(h.calls.filter((c) => c[0] === "permissions.request").length, 1);
    h.chrome.__granted = () => true;
    assert.deepEqual(await missingOrigins("http://nas.local:8080"), []);
    assert.equal(await grantAccess("http://nas.local:8080"), true);
    assert.equal(h.calls.filter((c) => c[0] === "permissions.request").length, 1); // no extra prompt
  } finally {
    h.uninstall();
  }
});

test("renderGrantAccess: renders nothing when everything is granted", async () => {
  const h = install();
  try {
    const container = { append() { assert.fail("should not render"); } };
    assert.equal(await renderGrantAccess(container, "https://a.example"), false);
  } finally {
    h.uninstall();
  }
});

// --- optionsLogic ----------------------------------------------------------

test("isPrivateHost: localhost, RFC1918, .local are private; public hosts are not", () => {
  for (const h of ["localhost", "127.0.0.1", "10.1.2.3", "172.16.0.1", "172.31.255.1", "192.168.1.5", "nas.local", "[::1]"]) {
    assert.equal(isPrivateHost(h), true, h);
  }
  for (const h of ["example.com", "8.8.8.8", "172.32.0.1", "192.169.1.1", "11.0.0.1"]) {
    assert.equal(isPrivateHost(h), false, h);
  }
});

test("normalizeBaseUrl: trims, rejects non-http(s), flags insecure plain http to public hosts", () => {
  assert.deepEqual(normalizeBaseUrl(" https://a.example/// "), { url: "https://a.example", insecure: false });
  assert.deepEqual(normalizeBaseUrl("http://nas.local:8080"), { url: "http://nas.local:8080", insecure: false });
  assert.deepEqual(normalizeBaseUrl("http://example.com"), { url: "http://example.com", insecure: true });
  assert.equal(normalizeBaseUrl("ftp://x"), null);
  assert.equal(normalizeBaseUrl("not a url"), null);
  assert.equal(normalizeBaseUrl(""), null);
});

test("describeMe: username + version, missing-perm warning, legacy fallback, errors", () => {
  const ok = (data) => ({ ok: true, status: 200, data, error: null });
  assert.deepEqual(describeMe(ok({ username: "alice", perms: ["library:write", "settings:write"], app_version: "0.8.0" })), {
    text: "Connected as alice · app v0.8.0.",
    kind: "ok",
    notices: [],
  });
  const warn = describeMe(ok({ username: "alice", perms: ["library:write"], app_version: "0.8.0" }));
  assert.equal(warn.kind, "warn");
  assert.match(warn.text, /missing: settings:write/);
  assert.equal(describeMe(ok({ username: null, legacy: true })).kind, "ok");
  assert.match(describeMe(ok({ username: null, legacy: true })).text, /older server/);
  const bad = describeMe({ ok: false, status: 401, data: null, error: "Token rejected" });
  assert.equal(bad.kind, "error");
  assert.match(bad.text, /401/);
});
