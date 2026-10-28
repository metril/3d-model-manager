/**
 * Minimal in-memory `chrome` stub for tests of storage/background/popup/options
 * logic. `install()` sets `globalThis.chrome` and returns a handle; call
 * `uninstall()` in a `finally`. Storage get/set yield briefly to expose races.
 */
export function install(overrides = {}) {
  const stores = { local: {}, session: {} };
  const listeners = {};
  const calls = [];
  const event = (name) => ({
    addListener: (fn) => {
      (listeners[name] ||= []).push(fn);
    },
  });
  const area = (name) => ({
    get: async (keys) => {
      const list = keys == null ? Object.keys(stores[name]) : [].concat(keys);
      const snap = {};
      for (const k of list) {
        if (k in stores[name]) snap[k] = structuredClone(stores[name][k]);
      }
      await new Promise((r) => setTimeout(r, 2));
      return snap;
    },
    set: async (obj) => {
      await new Promise((r) => setTimeout(r, 1));
      stores[name] = { ...stores[name], ...structuredClone(obj) };
    },
  });
  const chrome = {
    storage: { local: area("local"), session: area("session") },
    runtime: { id: "test-ext", getURL: (p = "") => `chrome-extension://test-ext/${p}` },
    permissions: {
      contains: async (q) => (chrome.__granted ? chrome.__granted(q) : true),
      request: async (q) => {
        calls.push(["permissions.request", q]);
        return true;
      },
    },
    cookies: { getAll: async () => [], get: async () => null, onChanged: event("cookies.onChanged") },
    tabs: { onUpdated: event("tabs.onUpdated") },
    ...overrides,
  };
  globalThis.chrome = chrome;
  return {
    chrome,
    stores,
    listeners,
    calls,
    uninstall() {
      delete globalThis.chrome;
    },
  };
}
