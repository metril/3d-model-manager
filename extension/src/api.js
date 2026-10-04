/**
 * Thin fetch client for the app's `/api/ext/*` plane. No `chrome.*` here —
 * takes `fetch` from the ambient global (available in both the MV3 service
 * worker and Node 18+/20+), so this stays unit-testable with a stubbed
 * `fetch` in Node's test runner without any DOM/extension shims.
 *
 * Every method returns a normalized result instead of throwing, so callers
 * (popup/background) never need a try/catch around a network call:
 *   { ok: boolean, status: number, data: unknown, error: string|null }
 */

/**
 * @param {{ baseUrl: string, token: string }} opts
 */
export function createClient({ baseUrl, token }) {
  const root = String(baseUrl ?? "").replace(/\/+$/, "");

  async function request(path, { method = "GET", body } = {}) {
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
      response = await fetch(`${root}/api/ext${path}`, {
        method,
        headers,
        body: payload,
        credentials: "omit",
        signal: AbortSignal.timeout(15000),
      });
    } catch (err) {
      // Network-level failure (offline, DNS, CORS, refused connection —
      // never logs the token, only the generic failure reason).
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
    // objects (each with a `msg`), not a string — join them instead of
    // letting `String(detail)` render "[object Object]".
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => (item && typeof item === "object" ? item.msg || JSON.stringify(item) : item))
        .join("; ");
    }
    return { ok: false, status: response.status, data, error: String(detail) };
  }

  return {
    ping() {
      return request("/ping");
    },
    createImport(url) {
      return request("/imports", { method: "POST", body: { url } });
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
