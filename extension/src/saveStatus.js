/**
 * Pure polling loop for the post-save status check. `POST /ext/imports` only
 * proves an Import ROW was created -- this polls `GET /ext/imports/{id}`
 * (via the injected `fetchStatus`) until the import reaches a terminal
 * state, so the popup can report what actually happened.
 *
 * No `chrome.*`/`fetch` here: `fetchStatus`, `sleep` and `now` are injected
 * so this stays unit-testable with fakes.
 *
 * Polling is DEADLINE-based (`now()` vs. `timeoutMs`), so slow requests
 * count against the budget, and each single request is capped by
 * `pollTimeoutMs`. 401/403/404 fail immediately (retrying can't help); any
 * other error or a non-terminal state keeps polling until the deadline.
 */

const DEFAULT_TIMEOUT_MS = 15000;
const DEFAULT_INTERVAL_MS = 2000;
const DEFAULT_POLL_TIMEOUT_MS = 5000;
const FATAL_STATUSES = new Set([401, 403, 404]);

/** Races `promise` against a timer so one hung request can't eat the deadline. */
function withPollTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(
      () => resolve({ ok: false, status: 0, data: null, error: "Status check timed out." }),
      ms,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/**
 * @param {object} opts
 * @param {(importId: string|number) => Promise<{ok: boolean, status: number, data: unknown, error: string|null}>} opts.fetchStatus
 *   Normalized `api.js`-shaped result -- never expected to throw.
 * @param {string|number} opts.importId
 * @param {number} [opts.timeoutMs]
 * @param {number} [opts.intervalMs]
 * @param {number} [opts.pollTimeoutMs] cap on a single status request
 * @param {(data: object) => void} [opts.onStatus] called with every successfully
 *   read status body (progress display / model id capture)
 * @param {(ms: number) => Promise<void>} opts.sleep
 * @param {() => number} [opts.now] clock (injectable for tests)
 * @returns {Promise<{outcome: "done"|"failed"|"timeout", error?: string}>}
 */
export async function pollImportStatus({
  fetchStatus,
  importId,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  intervalMs = DEFAULT_INTERVAL_MS,
  pollTimeoutMs = DEFAULT_POLL_TIMEOUT_MS,
  onStatus,
  sleep,
  now = Date.now,
}) {
  const deadline = now() + timeoutMs;

  for (let attempt = 0; ; attempt++) {
    if (attempt > 0) {
      if (now() + intervalMs >= deadline) {
        break;
      }
      await sleep(intervalMs);
    }

    const result = await withPollTimeout(Promise.resolve(fetchStatus(importId)), pollTimeoutMs);
    if (result && !result.ok && FATAL_STATUSES.has(result.status)) {
      return { outcome: "failed", error: result.error || `Status check failed (${result.status}).` };
    }
    const data = result && result.ok ? result.data : null;
    const state = data && typeof data === "object" ? data.state : null;
    if (state) {
      onStatus?.(data);
    }

    if (state === "done") {
      return { outcome: "done" };
    }
    if (state === "failed") {
      return { outcome: "failed", error: (data && data.error) || "Import failed." };
    }
    // Non-terminal state or a transient error: keep polling until the deadline.
  }

  return { outcome: "timeout" };
}
