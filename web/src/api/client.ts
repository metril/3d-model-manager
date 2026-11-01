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
  /** Machine code from `{code, detail}` bodies (printer control routes), else undefined. */
  readonly code?: string;
  /** Offending files of a `does_not_fit` refusal (batch dispatch). */
  readonly files?: { id: number; name: string }[];

  constructor(status: number, detail: string, code?: string, files?: { id: number; name: string }[]) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.files = files;
  }
}

const API_BASE = "/api";

interface JsonRequestInit extends Omit<RequestInit, "body"> {
  body?: unknown;
}

function isUnauthenticatedRedirectExempt(): boolean {
  return window.location.pathname === "/login";
}

async function extractError(response: Response): Promise<{ detail: string; code?: string; files?: { id: number; name: string }[] }> {
  try {
    const data: unknown = await response.json();
    if (data !== null && typeof data === "object" && "detail" in data) {
      const { detail, code } = data as { detail: unknown; code?: unknown };
      if (typeof detail === "string") {
        return { detail, code: typeof code === "string" ? code : undefined };
      }
      // FastAPI validation errors (422): a list of {loc, msg}.
      if (Array.isArray(detail)) {
        const parts = detail.map((item) => {
          if (item === null || typeof item !== "object") return String(item);
          const { loc, msg } = item as { loc?: unknown; msg?: unknown };
          const where = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : "";
          const text = typeof msg === "string" ? msg : JSON.stringify(item);
          return where ? `${where}: ${text}` : text;
        });
        if (parts.length > 0) return { detail: parts.join("; ") };
      }
      // Coded errors from the 2FA routes: `detail: {code, message}`.
      if (detail !== null && typeof detail === "object") {
        const d = detail as { code?: unknown; message?: unknown; files?: unknown };
        return {
          detail: typeof d.message === "string" ? d.message : response.statusText || `Request failed with status ${response.status}`,
          code: typeof d.code === "string" ? d.code : undefined,
          files: Array.isArray(d.files) ? (d.files as { id: number; name: string }[]) : undefined,
        };
      }
    }
  } catch {
    // Response body wasn't JSON (or was empty) — fall through to statusText.
  }
  return { detail: response.statusText || `Request failed with status ${response.status}` };
}

/** Fired on a 403 `2fa_enrollment_required`; AppShell refreshes `me` so the
 * enrolment gate renders (it ignores the event once the gate is already up). */
export const ENROLL_2FA_EVENT = "tdmm:2fa-enrollment-required";

/** Shared 401/error/empty-body handling for both the JSON path (`request`)
 * and the raw-body path (`requestRaw`, e.g. the cover-snapshot upload) --
 * everything past "here's a `Response`" is identical between the two. */
async function handleResponse<T>(response: Response): Promise<T> {
  if (response.status === 401 && !isUnauthenticatedRedirectExempt()) {
    window.location.assign("/login");
    throw new ApiError(401, (await extractError(response)).detail);
  }

  if (!response.ok) {
    const { detail, code, files } = await extractError(response);
    if (response.status === 403 && code === "2fa_enrollment_required") {
      window.dispatchEvent(new Event(ENROLL_2FA_EVENT));
    }
    throw new ApiError(response.status, detail, code, files);
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
async function requestForm<T>(path: string, formData: FormData, method: "POST" | "PUT" = "POST"): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
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
  /** PUT a multipart `FormData` body. */
  putForm: <T>(path: string, formData: FormData) => requestForm<T>(path, formData, "PUT"),
};
