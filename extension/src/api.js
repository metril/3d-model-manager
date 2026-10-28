/**
 * Thin fetch client for the app's `/api/ext/*` plane. No `chrome.*` here —
 * takes `fetch` from the ambient global (available in both the MV3 service
 * worker and Node 18+/20+), so this stays unit-testable with a stubbed
 * `fetch` in Node's test runner without any DOM/extension shims.
 *
 * `v2: true` requests target the scoped-key plane (`/api/ext/v2`, used with
 * the optional "Printer key"); the same client class serves both.
 *
 * Every method returns a normalized result instead of throwing, so callers
 * (popup/background) never need a try/catch around a network call:
 *   { ok: boolean, status: number, data: unknown, error: string|null }
 */

/** Adds a status-bearing prefix for the statuses users can act on. */
export function friendlyError(status, detail) {
  if (status === 401) return `Token rejected (401) — check your API token. ${detail}`;
  if (status === 403) return `Token lacks permission (403) — ${detail}`;
  if (status === 422) return `Invalid request (422): ${detail}`;
  if (status >= 500) return `Server error (${status}): ${detail}`;
  return detail;
}

/**
 * @param {{ baseUrl: string, token: string, retryDelayMs?: number }} opts
 */
export function createClient({ baseUrl, token, retryDelayMs = 400 }) {
  const root = String(baseUrl ?? "").replace(/\/+$/, "");

  /**
   * One request with a single retry (after `retryDelayMs`) on a transient
   * failure -- network error, timeout or 5xx -- unless `retry: false`
   * (non-idempotent POSTs, i.e. `createImport`).
   */
  async function request(path, opts = {}) {
    const { retry = true } = opts;
    const first = await requestOnce(path, opts);
    if (!retry || first.ok || (first.status !== 0 && first.status < 500)) {
      return first;
    }
    await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    return requestOnce(path, opts);
  }

  async function requestOnce(path, { method = "GET", body, v2 = false } = {}) {
    const headers = {
      Authorization: `Bearer ${token}`,
    };
    let payload;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(`${root}${v2 ? "/api/ext/v2" : "/api/ext"}${path}`, {
        method,
        headers,
        body: payload,
        credentials: "omit",
        // A redirect would re-send the bearer token to wherever it points.
        redirect: "error",
        signal: AbortSignal.timeout(15000),
      });
    } catch (err) {
      // Network-level failure (offline, DNS, CORS, refused connection —
      // never logs the token, only the generic failure reason).
      if (err?.name === "TimeoutError" || err?.name === "AbortError") {
        return { ok: false, status: 0, data: null, error: "Request timed out — is the app reachable?" };
      }
      return { ok: false, status: 0, data: null, error: err?.message || "Network error" };
    }

    let data = null;
    try {
      data = await response.json();
    } catch {
      // Non-JSON or empty body is fine for a 2xx with no content; for a
      // non-2xx with no JSON body we fall back to statusText below.
      data = null;
    }

    if (response.ok) {
      return { ok: true, status: response.status, data, error: null };
    }

    let detail =
      (data && typeof data === "object" && "detail" in data && data.detail) ||
      response.statusText ||
      `Request failed (${response.status})`;
    // FastAPI validation errors surface `detail` as an array of error
    // objects (each with a `msg`); other shapes may be plain objects.
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => (item && typeof item === "object" ? item.msg || JSON.stringify(item) : item))
        .join("; ");
    } else if (detail && typeof detail === "object") {
      detail = JSON.stringify(detail);
    }
    return { ok: false, status: response.status, data, error: friendlyError(response.status, String(detail)) };
  }

  return {
    ping() {
      return request("/ping");
    },
    /**
     * `GET /ext/me` -> `{username, perms, app_version}`. Older servers
     * without it (404) fall back to `/ping`, reported as `{legacy: true}`.
     */
    async me() {
      const result = await request("/me");
      if (result.status !== 404) {
        return result;
      }
      const ping = await request("/ping");
      return ping.ok
        ? { ...ping, data: { username: null, perms: null, app_version: null, legacy: true } }
        : ping;
    },
    /**
     * @param {string} url
     * @param {{collectionId?: number|null, tags?: string[]}} [options] applied
     *   by the app when the import finishes (ignored on a dedup 200).
     */
    createImport(url, { collectionId = null, tags = [] } = {}) {
      const body = { url };
      if (collectionId !== null && collectionId !== undefined) body.collection_id = collectionId;
      if (tags.length > 0) body.tags = tags;
      // Not idempotent: never auto-retried.
      return request("/imports", { method: "POST", body, retry: false });
    },
    /** The app's followed collections, `[{id, name}]`. */
    listLocalCollections() {
      return request("/local-collections");
    },
    /** Existing tags, `[{name}]`, for autocomplete. */
    listTags() {
      return request("/tags");
    },
    /** v2 (scoped printer key): key info incl. `scopes`. */
    v2Ping() {
      return request("/ping", { v2: true });
    },
    v2Printers() {
      return request("/printers", { v2: true });
    },
    /** Queue-eligible (sliced) files of a model: `[{file_id, name, kind, plate}]`. */
    v2Printables(modelId) {
      return request(`/models/${encodeURIComponent(modelId)}/printables`, { v2: true });
    },
    /** Not idempotent: never auto-retried. `plate` omitted when null. */
    v2Enqueue({ modelId, fileId, plate = null, printerId = null }) {
      const body = { model_id: modelId, file_id: fileId };
      if (plate !== null && plate !== undefined) body.plate = plate;
      if (printerId !== null && printerId !== undefined) body.printer_id = printerId;
      return request("/queue", { method: "POST", body, retry: false, v2: true });
    },
    /**
     * Narrow status read for the popup's post-save poll (import-health
     * branch T4) -- `{id, state, error}` only, matching the token plane's
     * deliberately narrow response (see `app.api.ext.get_import_status`).
     * @param {string|number} importId
     */
    getImportStatus(importId) {
      return request(`/imports/${encodeURIComponent(importId)}`);
    },
    setMakerworldCredential(cookieValue) {
      return request("/credentials/makerworld", { method: "POST", body: { token: cookieValue } });
    },
    /**
     * @param {string} site
     * @param {Array<{list_id: string, title: string, slug: string|null, count: number|null, is_default: boolean}>} collections
     */
    pushCollections(site, collections) {
      return request("/collections", { method: "POST", body: { site, collections } });
    },
    /**
     * Upserts ONE cached collection entry (no delete of the others), unlike
     * `pushCollections`, which is a full replace for the site.
     * @param {string} site
     * @param {{list_id: string, title: string, slug: string|null, count: number|null, is_default: boolean}} entry
     */
    upsertCollection(site, entry) {
      const { list_id, ...rest } = entry;
      return request(`/collections/${encodeURIComponent(list_id)}`, {
        method: "PUT",
        body: { site, ...rest },
      });
    },
    /**
     * @param {string} site
     * @param {string} listId
     * @param {Array<{external_id: string, title: string, url: string, author: string|null, thumbnail_url: string|null}>} items
     */
    pushCollectionItems(site, listId, items) {
      return request(`/collections/${encodeURIComponent(listId)}/items`, {
        method: "POST",
        body: { site, items },
      });
    },
  };
}
