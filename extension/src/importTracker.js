/**
 * Background import tracking. The service worker owns the status poll so it
 * survives the popup closing; progress lives in `chrome.storage.session`
 * (one `imp:<id>` record per import) and the popup just subscribes to
 * `storage.onChanged`. Every dependency (`api`, badge setter, clock, sleep)
 * is injected, so this is unit-testable with fakes + the chrome stub.
 *
 * Keep-alive: the poll loop's own fetches keep the worker awake while an
 * import runs; a long import outlives one `trackImport` run (`timeoutMs`),
 * and a 1-minute alarm (see background.js) resumes every unfinished record.
 *
 * Record: { id, url, tabId, state, error, modelId, startedAt, finished,
 *           queue: null | {printerId, unassigned?}, queueResult: null | {status, message?, count?} }
 */

import { pollImportStatus } from "./saveStatus.js";
import { queueModel } from "./printQueue.js";

export const IMPORT_PREFIX = "imp:";
export const URL_PREFIX = "imp-url:";
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const GIVE_UP_AFTER_MS = 30 * 60 * 1000;
const BADGE_OK = "#2e7d32";
const BADGE_ERR = "#b3261e";
const BADGE_BUSY = "#475569";

const session = () => chrome.storage.session;

export async function readImport(id) {
  const key = IMPORT_PREFIX + id;
  return (await session().get(key))[key] ?? null;
}

/** Latest import record for a page URL (popup re-open), or null. */
export async function findImportForUrl(url) {
  const key = URL_PREFIX + url;
  const id = (await session().get(key))[key];
  return id === undefined ? null : readImport(id);
}

/** Merge-writes one record. Single writer per id (callers hold `import:<id>`). */
export async function patchImport(id, patch) {
  const current = (await readImport(id)) ?? { id };
  const next = { ...current, ...patch };
  await session().set({ [IMPORT_PREFIX + id]: next });
  return next;
}

/** All unfinished records, for alarm/worker-start resume. */
export async function listActiveImports() {
  const all = await session().get(null);
  return Object.entries(all)
    .filter(([k, v]) => k.startsWith(IMPORT_PREFIX) && v && !v.finished)
    .map(([, v]) => v);
}

async function badge(deps, rec, text, color) {
  try {
    await deps.setBadge(rec.tabId, text, color);
  } catch {
    // The tab may be gone; the badge is best-effort.
  }
}

/** Runs the queue step and badges the outcome. */
export async function runQueue(id, deps) {
  const rec = await readImport(id);
  if (!rec || rec.modelId == null) {
    return patchImport(id, { queueResult: { status: "error", message: "The import has no model to queue." } });
  }
  let result;
  try {
    const { lastSave } = await deps.getState();
    result = await queueModel({
      api: deps.v2Api,
      modelId: rec.modelId,
      // `unassigned`: the popup explicitly chose no printer; only the
      // context-menu path (printerId null, no marker) falls back to lastSave.
      printerId: rec.queue?.unassigned ? null : (rec.queue?.printerId ?? lastSave.printerId ?? null),
    });
  } catch (err) {
    result = { status: "error", message: err?.message || "Couldn't add to the queue." };
  }
  const next = await patchImport(id, { queueResult: result });
  await badge(deps, next, result.status === "queued" ? "✓" : "!", result.status === "queued" ? BADGE_OK : BADGE_ERR);
  return next;
}

/**
 * Polls one import to a terminal state, mirroring progress into storage and
 * the tab badge; on `done` runs the queue step when the save asked for it.
 * @param {number|string} id
 * @param {{api: object, v2Api?: object, setBadge: Function, getState: Function,
 *          sleep: Function, now?: () => number, timeoutMs?: number, intervalMs?: number}} deps
 */
export async function trackImport(id, deps) {
  const now = deps.now ?? Date.now;
  let rec = await readImport(id);
  if (!rec || rec.finished) {
    return rec;
  }
  await badge(deps, rec, "…", BADGE_BUSY);

  let writes = Promise.resolve();
  const outcome = await pollImportStatus({
    fetchStatus: (i) => deps.api.getImportStatus(i),
    importId: id,
    timeoutMs: deps.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    intervalMs: deps.intervalMs,
    sleep: deps.sleep,
    now,
    onStatus: (data) => {
      writes = writes.then(() =>
        patchImport(id, { state: data.state, modelId: data.model_id ?? null, error: data.error ?? null }),
      );
    },
  });
  await writes;

  if (outcome.outcome === "done") {
    const queueNow = Boolean(rec.queue && deps.v2Api && !rec.queueResult);
    // Not finished until the queue step has run, so a worker killed mid-queue
    // is resumed by the alarm (which re-enters here with queueResult unset).
    rec = await patchImport(id, { state: "done", finished: !queueNow });
    await badge(deps, rec, "✓", BADGE_OK);
    if (queueNow) {
      await runQueue(id, deps);
      rec = await patchImport(id, { finished: true });
    }
  } else if (outcome.outcome === "failed") {
    rec = await patchImport(id, { state: "failed", error: outcome.error, finished: true });
    await badge(deps, rec, "!", BADGE_ERR);
  } else if (now() - rec.startedAt > GIVE_UP_AFTER_MS) {
    rec = await patchImport(id, { finished: true, gaveUp: true });
    await badge(deps, rec, "", BADGE_BUSY);
  } else {
    rec = await readImport(id); // still running: the alarm resumes it
  }
  return rec;
}

/**
 * Creates the import and registers it for tracking; resolves with the
 * `createImport` result plus `importId`. Never throws. The caller kicks
 * `trackImport` (so it can wrap it in `runOnce`).
 * @param {{url: string, tabId?: number, options?: object, queue?: {printerId: number|null}|null}} req
 */
export async function startSave({ url, tabId, options, queue = null }, deps) {
  const res = await deps.api.createImport(url, options);
  const data = res.data;
  if (!res.ok || !data || data.id === undefined || data.id === null) {
    return res;
  }
  const now = deps.now ?? Date.now;
  await session().set({
    [IMPORT_PREFIX + data.id]: {
      id: data.id,
      url,
      tabId: tabId ?? null,
      state: data.state ?? "pending",
      error: null,
      modelId: data.model_id ?? null,
      startedAt: now(),
      finished: false,
      deduped: res.status !== 201,
      queue,
      queueResult: null,
    },
    [URL_PREFIX + url]: data.id,
  });
  return { ...res, importId: data.id };
}
