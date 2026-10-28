import { test } from "node:test";
import assert from "node:assert/strict";

import { pollImportStatus } from "../src/saveStatus.js";

/** Fake clock: `sleep` advances `now` instead of waiting. */
let clock = 0;
const now = () => clock;
function fakeSleep(calls) {
  return async (ms) => {
    calls.push(ms);
    clock += ms;
  };
}

test("pollImportStatus: 401/403/404 fail immediately without further polling", async () => {
  for (const status of [401, 403, 404]) {
    let calls = 0;
    const result = await pollImportStatus({
      fetchStatus: async () => {
        calls++;
        return { ok: false, status, data: null, error: `bad ${status}` };
      },
      importId: 1,
      now,
      sleep: fakeSleep([]),
    });
    assert.deepEqual(result, { outcome: "failed", error: `bad ${status}` });
    assert.equal(calls, 1);
  }
});

test("pollImportStatus: slow requests count against the deadline (clock-based)", async () => {
  clock = 0;
  let calls = 0;
  const result = await pollImportStatus({
    fetchStatus: async () => {
      calls++;
      clock += 5000;
      return { ok: true, status: 200, data: { state: "fetching" }, error: null };
    },
    importId: 1,
    timeoutMs: 15000,
    intervalMs: 2000,
    now,
    sleep: fakeSleep([]),
  });
  assert.deepEqual(result, { outcome: "timeout" });
  assert.equal(calls, 3); // not ceil(15000/2000)=8
});

test("pollImportStatus: a hanging request is cut off by the per-poll timeout", async () => {
  clock = 0;
  let calls = 0;
  const result = await pollImportStatus({
    fetchStatus: () => {
      calls++;
      return new Promise(() => {});
    },
    importId: 1,
    timeoutMs: 1,
    pollTimeoutMs: 10,
    now,
    sleep: fakeSleep([]),
  });
  assert.deepEqual(result, { outcome: "timeout" });
  assert.equal(calls, 1);
});

test("pollImportStatus: a deduped (200) import's state is honoured like a fresh one", async () => {
  const result = await pollImportStatus({
    fetchStatus: async () => ({ ok: true, status: 200, data: { id: 3, state: "failed", error: "old failure" }, error: null }),
    importId: 3,
    now,
    sleep: fakeSleep([]),
  });
  assert.deepEqual(result, { outcome: "failed", error: "old failure" });
});

test("pollImportStatus: resolves done as soon as the state is done", async () => {
  const sleeps = [];
  let calls = 0;
  const result = await pollImportStatus({
    fetchStatus: async () => {
      calls++;
      return { ok: true, status: 200, data: { id: 1, state: "done", error: null }, error: null };
    },
    importId: 1,
    now, sleep: fakeSleep(sleeps),
  });

  assert.deepEqual(result, { outcome: "done" });
  assert.equal(calls, 1);
  assert.deepEqual(sleeps, []); // resolved on the first poll -- never slept
});

test("pollImportStatus: resolves failed with the row's error text", async () => {
  const result = await pollImportStatus({
    fetchStatus: async () => ({
      ok: true,
      status: 200,
      data: {
        id: 1,
        state: "failed",
        error: "Bambu sign-in expired — reconnect your Bambu account in Settings.",
      },
      error: null,
    }),
    importId: 1,
    now, sleep: fakeSleep([]),
  });

  assert.deepEqual(result, {
    outcome: "failed",
    error: "Bambu sign-in expired — reconnect your Bambu account in Settings.",
  });
});

test("pollImportStatus: a failed state with no error text falls back to a generic message", async () => {
  const result = await pollImportStatus({
    fetchStatus: async () => ({
      ok: true,
      status: 200,
      data: { id: 1, state: "failed", error: null },
      error: null,
    }),
    importId: 1,
    now, sleep: fakeSleep([]),
  });

  assert.deepEqual(result, { outcome: "failed", error: "Import failed." });
});

test("pollImportStatus: times out if the import never leaves a non-terminal state", async () => {
  const sleeps = [];
  let calls = 0;
  const result = await pollImportStatus({
    fetchStatus: async () => {
      calls++;
      return {
        ok: true,
        status: 200,
        data: { id: 1, state: "fetching", error: null },
        error: null,
      };
    },
    importId: 1,
    timeoutMs: 6000,
    intervalMs: 2000,
    now, sleep: fakeSleep(sleeps),
  });

  assert.deepEqual(result, { outcome: "timeout" });
  assert.equal(calls, 3); // ceil(6000/2000) attempts
  assert.deepEqual(sleeps, [2000, 2000]); // one sleep between each attempt, none after the last
});

test("pollImportStatus: a transient fetch error mid-poll is retried, not surfaced immediately", async () => {
  const sleeps = [];
  const outcomes = [
    { ok: false, status: 0, data: null, error: "Network error" },
    { ok: true, status: 200, data: { id: 1, state: "done", error: null }, error: null },
  ];
  let calls = 0;
  const result = await pollImportStatus({
    fetchStatus: async () => outcomes[calls++],
    importId: 1,
    timeoutMs: 6000,
    intervalMs: 2000,
    now, sleep: fakeSleep(sleeps),
  });

  assert.deepEqual(result, { outcome: "done" });
  assert.equal(calls, 2);
  assert.deepEqual(sleeps, [2000]); // retried once after the transient failure
});

test("pollImportStatus: fetch errors all the way to the deadline still resolve to timeout", async () => {
  let calls = 0;
  const result = await pollImportStatus({
    fetchStatus: async () => {
      calls++;
      return { ok: false, status: 0, data: null, error: "Network error" };
    },
    importId: 1,
    timeoutMs: 4000,
    intervalMs: 2000,
    now, sleep: fakeSleep([]),
  });

  assert.deepEqual(result, { outcome: "timeout" });
  assert.equal(calls, 2); // ceil(4000/2000)
});
