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
  const changed = event("storage.onChanged");
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
      const changes = {};
      for (const k of Object.keys(obj)) {
        changes[k] = { oldValue: stores[name][k], newValue: structuredClone(obj[k]) };
      }
      stores[name] = { ...stores[name], ...structuredClone(obj) };
      for (const fn of listeners["storage.onChanged"] || []) fn(changes, name);
    },
    remove: async (keys) => {
      for (const k of [].concat(keys)) delete stores[name][k];
    },
  });
  const chrome = {
    storage: { local: area("local"), session: area("session"), onChanged: changed },
    runtime: {
      id: "test-ext",
      getURL: (p = "") => `chrome-extension://test-ext/${p}`,
      onInstalled: event("runtime.onInstalled"),
      onStartup: event("runtime.onStartup"),
      onMessage: event("runtime.onMessage"),
      openOptionsPage: async () => {
        calls.push(["runtime.openOptionsPage"]);
      },
    },
    permissions: {
      contains: async (q) => (chrome.__granted ? chrome.__granted(q) : true),
      request: async (q) => {
        calls.push(["permissions.request", q]);
        return true;
      },
    },
    cookies: {
      getAll: async () => [],
      get: async () => null,
      getAllCookieStores: async () => [{ id: "0" }],
      onChanged: event("cookies.onChanged"),
    },
    tabs: { onUpdated: event("tabs.onUpdated"), query: async () => [] },
    webNavigation: { onHistoryStateUpdated: event("webNavigation.onHistoryStateUpdated") },
    alarms: {
      get: async () => null,
      create: (name, info) => calls.push(["alarms.create", name, info]),
      clear: async (name) => {
        calls.push(["alarms.clear", name]);
      },
      onAlarm: event("alarms.onAlarm"),
    },
    action: {
      setBadgeText: async (a) => {
        calls.push(["action.setBadgeText", a]);
      },
      setBadgeBackgroundColor: async (a) => {
        calls.push(["action.setBadgeBackgroundColor", a]);
      },
    },
    contextMenus: {
      removeAll: (cb) => cb && cb(),
      create: (o) => calls.push(["contextMenus.create", o]),
      onClicked: event("contextMenus.onClicked"),
    },
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
