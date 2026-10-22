/**
 * Thin fetch wrapper for the `/api` backend.
 *
 * - Always sends cookies (`credentials: "include"`) so the `tdmm_session`
 *   cookie round-trips.
 * - Serializes/deserializes JSON automatically.
 * - Throws a typed `ApiError { status, detail }` for any non-2xx response.
 * - On 401 (the backend's "not authenticated at all" signal — see
 *   `backend/app/api/deps.py`), hard-redirects to `/login`, *unless* we're
 *   already there. That carve-out matters because the login page itself
 *   calls `GET /auth/me` (via `useAuth`) to decide what to render; a 401
 *   from that call is the expected, unauthenticated case and must not
 *   trigger a redirect loop back to the page we're already on.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const API_BASE = "/api";

interface JsonRequestInit extends Omit<RequestInit, "body"> {
  body?: unknown;
}

function isUnauthenticatedRedirectExempt(): boolean {
  return window.location.pathname === "/login";
}

async function extractDetail(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (
      data !== null &&
      typeof data === "object" &&
      "detail" in data &&
      typeof (data as { detail: unknown }).detail === "string"
    ) {
      return (data as { detail: string }).detail;
    }
  } catch {
    // Response body wasn't JSON (or was empty) — fall through to statusText.
  }
  return response.statusText || `Request failed with status ${response.status}`;
}

/** Shared 401/error/empty-body handling for both the JSON path (`request`)
 * and the raw-body path (`requestRaw`, e.g. the cover-snapshot upload) --
 * everything past "here's a `Response`" is identical between the two. */
async function handleResponse<T>(response: Response): Promise<T> {
  if (response.status === 401 && !isUnauthenticatedRedirectExempt()) {
    window.location.assign("/login");
    throw new ApiError(401, await extractDetail(response));
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }

  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function request<T>(path: string, init: JsonRequestInit = {}): Promise<T> {
  const { body, headers, ...rest } = init;

  const response = await fetch(`${API_BASE}${path}`, {
    ...rest,
    credentials: "include",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  return handleResponse<T>(response);
}

/** Raw (non-JSON) request body -- e.g. the cover-snapshot endpoint, which
 * takes a raw PNG stream rather than a JSON envelope (`backend/app/api/
 * models.py`'s `POST /models/{slug}/cover`, R13a). */
async function requestRaw<T>(path: string, method: string, body: BodyInit, contentType: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: "include",
    headers: { "Content-Type": contentType },
    body,
  });

  return handleResponse<T>(response);
}

/** POST a `FormData` body (e.g. a profile bundle zip) -- the browser sets
 * its own `multipart/form-data; boundary=...` header, so this must NOT
 * pass a `Content-Type` the way `requestRaw` does. */
async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    body: formData,
  });
  return handleResponse<T>(response);
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** POST with a raw (non-JSON) body, e.g. an image blob. */
  postRaw: <T>(path: string, body: BodyInit, contentType: string) => requestRaw<T>(path, "POST", body, contentType),
  /** POST a multipart `FormData` body. */
  postForm: <T>(path: string, formData: FormData) => requestForm<T>(path, formData),
};
