/**
 * Persistence over `chrome.storage.local`, split into two keys:
 *   - `config`: user settings + the API token (written by the options page).
 *       { schemaVersion, appBaseUrl, apiToken, autoCourier, autoSyncCollections }
 *   - `state`: machine-written bookkeeping (throttle hashes, courier status).
 *       { lastMakerworldHash, lastCollectionsHash,
 *         lastCollectionItemsHash: Array<{listId, hash}>,
 *         lastCourierStatus, lastCourierAt }
 * The split keeps the background worker's frequent state writes from ever
 * clobbering a settings save made in the options page (and vice versa).
 *
 * Every read-modify-write runs under `navigator.locks.request("tdmm-state")`
 * so it is atomic ACROSS extension contexts (service worker, popup, options).
 * Where Web Locks are unavailable (node tests) it falls back to an
 * in-context promise chain.
 *
 * `lastCollectionItemsHash` is an ARRAY of pairs, not an object keyed by
 * listId: list ids look like integers and JS engines reorder integer-like
 * keys, which would break "prune to the last 50 by recency".
 *
 * Only a HASH of the MakerWorld cookie is ever stored, never the cookie.
 */

const CONFIG_KEY = "config";
const STATE_KEY = "state";
const LOCK_NAME = "tdmm-state";
export const SCHEMA_VERSION = 2;

const str = (v, d) => (typeof v === "string" ? v : d);
const bool = (v, d) => (typeof v === "boolean" ? v : d);
const strOrNull = (v) => (typeof v === "string" && v ? v : null);

/** Coerces an untrusted stored blob into a well-typed config object. */
export function coerceConfig(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  return {
    schemaVersion: SCHEMA_VERSION,
    appBaseUrl: str(r.appBaseUrl, ""),
    apiToken: str(r.apiToken, ""),
    autoCourier: bool(r.autoCourier, true),
    autoSyncCollections: bool(r.autoSyncCollections, true),
  };
}

/** Coerces an untrusted stored blob into a well-typed state object. */
export function coerceState(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  const items = Array.isArray(r.lastCollectionItemsHash)
    ? r.lastCollectionItemsHash.filter(
        (e) => e && typeof e.listId === "string" && typeof e.hash === "string",
      )
    : [];
  return {
    lastMakerworldHash: strOrNull(r.lastMakerworldHash),
    lastCollectionsHash: strOrNull(r.lastCollectionsHash),
    lastCollectionItemsHash: items,
    lastCourierStatus: strOrNull(r.lastCourierStatus),
    lastCourierAt: typeof r.lastCourierAt === "number" ? r.lastCourierAt : null,
  };
}

let writeChain = Promise.resolve();

/** Runs `fn` exclusively: Web Locks across contexts, promise chain otherwise. */
function withLock(fn) {
  const locks = globalThis.navigator?.locks;
  if (locks && typeof locks.request === "function") {
    return locks.request(LOCK_NAME, fn);
  }
  const run = writeChain.then(fn);
  writeChain = run.catch(() => {});
  return run;
}

/**
 * Reads both keys. A pre-split blob (hashes inside `config`, no
 * `schemaVersion`) is reported via `needsMigration`; its hashes seed `state`.
 */
async function readAll() {
  const stored = await chrome.storage.local.get([CONFIG_KEY, STATE_KEY]);
  const rawConfig = stored[CONFIG_KEY];
  const rawState = stored[STATE_KEY];
  const needsMigration = Boolean(rawConfig) && rawConfig.schemaVersion !== SCHEMA_VERSION;
  return {
    config: coerceConfig(rawConfig),
    state: coerceState(needsMigration ? { ...rawConfig, ...(rawState || {}) } : rawState),
    needsMigration,
  };
}

async function readMigrated() {
  const first = await readAll();
  if (!first.needsMigration) {
    return first;
  }
  return withLock(async () => {
    const again = await readAll();
    if (again.needsMigration) {
      await chrome.storage.local.set({ [CONFIG_KEY]: again.config, [STATE_KEY]: again.state });
    }
    return again;
  });
}

export async function getConfig() {
  return (await readMigrated()).config;
}

export async function getState() {
  return (await readMigrated()).state;
}

/** Atomically shallow-merges `patch` into the persisted config. */
export function setConfig(patch) {
  return withLock(async () => {
    const { config, state } = await readAll();
    const next = coerceConfig({ ...config, ...patch });
    await chrome.storage.local.set({ [CONFIG_KEY]: next, [STATE_KEY]: state });
    return next;
  });
}

/**
 * Atomically updates the persisted state. `update` is either a patch object
 * or an updater `(current) => patch` evaluated INSIDE the lock, so
 * read-modify-write sequences (e.g. upserting one hash into an array) can't
 * lose a concurrent writer's changes.
 * @param {object|((current: object) => object)} update
 */
export function setState(update) {
  return withLock(async () => {
    const { config, state } = await readAll();
    const patch = typeof update === "function" ? update(state) : update;
    const next = coerceState({ ...state, ...patch });
    await chrome.storage.local.set({ [CONFIG_KEY]: config, [STATE_KEY]: next });
    return next;
  });
}

/**
 * State patch that clears the throttle hashes when the app URL or token
 * changes, so a new server gets a fresh push instead of being skipped as
 * "unchanged".
 */
export function hashResetPatch(prev, next) {
  if (prev.appBaseUrl === next.appBaseUrl && prev.apiToken === next.apiToken) {
    return {};
  }
  return { lastMakerworldHash: null, lastCollectionsHash: null, lastCollectionItemsHash: [] };
}

/** True once both an app base URL and an API token have been entered. */
export function isConfigured(config) {
  return Boolean(config?.appBaseUrl && config?.apiToken);
}
