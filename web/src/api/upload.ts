/**
 * `XMLHttpRequest`-based upload PUT (fetch has no upload progress events —
 * Task 8 decision). Exposed as a plain function with an injectable
 * `{ onProgress }` callback so tests can fake the uploader instead of
 * mocking the global `XMLHttpRequest`.
 */
import type { DuplicateUploadOut, UploadResult } from "@/api/types";

export interface UploadParams {
  modelId: number;
  revisionId: number;
  relPath: string;
  file: Blob;
  replace?: boolean;
  /** Bypasses the content-hash duplicate check (R11-C item 18) -- the
   * "Upload anyway" retry after a `DuplicateUploadError`. */
  allowDuplicate?: boolean;
}

export interface UploadCallbacks {
  onProgress?: (loaded: number, total: number) => void;
  /** Aborts the in-flight request; the promise rejects with `UploadAbortedError`. */
  signal?: AbortSignal;
  /** Abort when no upload progress arrives for this long, in ms (default `UPLOAD_IDLE_TIMEOUT_MS`). */
  timeoutMs?: number;
}

/** Idle watchdog, not an absolute cap: slow links may upload for hours as long as bytes keep flowing. */
export const UPLOAD_IDLE_TIMEOUT_MS = 5 * 60 * 1000;

export class UploadAbortedError extends Error {
  constructor() {
    super("upload cancelled");
    this.name = "UploadAbortedError";
  }
}

export type UploadFn = (params: UploadParams, callbacks?: UploadCallbacks) => Promise<UploadResult>;

/** Thrown instead of a plain `Error` when the upload 409s because this
 * content's blake3 hash already exists elsewhere in the library -- carries
 * the structured body so the UI can render "Already in your library" with
 * a link + retry, instead of just a generic failure message. */
export class DuplicateUploadError extends Error {
  readonly existing: DuplicateUploadOut["existing"];
  readonly suggestedName: string;

  constructor(body: DuplicateUploadOut) {
    super("duplicate content");
    this.name = "DuplicateUploadError";
    this.existing = body.existing;
    this.suggestedName = body.suggested_name;
  }
}

function parseJson(xhr: XMLHttpRequest): unknown {
  try {
    return JSON.parse(xhr.responseText);
  } catch {
    return null;
  }
}

function extractErrorDetail(xhr: XMLHttpRequest): string | null {
  const data = parseJson(xhr);
  if (data !== null && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string") {
    return (data as { detail: string }).detail;
  }
  return null;
}

function isDuplicateUploadBody(data: unknown): data is DuplicateUploadOut {
  return (
    data !== null &&
    typeof data === "object" &&
    (data as { detail?: unknown }).detail === "duplicate" &&
    typeof (data as { existing?: unknown }).existing === "object"
  );
}

export const uploadFile: UploadFn = (params, callbacks = {}) => {
  return new Promise((resolve, reject) => {
    const { signal } = callbacks;
    if (signal?.aborted) {
      reject(new UploadAbortedError());
      return;
    }
    const searchParams = new URLSearchParams({
      model_id: String(params.modelId),
      revision_id: String(params.revisionId),
      rel_path: params.relPath,
    });
    if (params.replace) searchParams.set("replace", "true");
    if (params.allowDuplicate) searchParams.set("allow_duplicate", "true");

    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/uploads?${searchParams.toString()}`);
    xhr.withCredentials = true;
    const idleMs = callbacks.timeoutMs ?? UPLOAD_IDLE_TIMEOUT_MS;
    let timedOut = false;
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const armIdle = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        timedOut = true;
        xhr.abort();
      }, idleMs);
    };
    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    const cleanup = () => {
      clearTimeout(idleTimer);
      signal?.removeEventListener("abort", onAbort);
    };
    armIdle();
    xhr.upload.onprogress = (event) => {
      armIdle();
      if (event.lengthComputable) {
        callbacks.onProgress?.(event.loaded, event.total);
      }
    };
    xhr.onload = () => {
      cleanup();
      if (xhr.status === 401) {
        if (window.location.pathname !== "/login") window.location.assign("/login");
        reject(new Error(extractErrorDetail(xhr) ?? "not authenticated"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadResult);
        } catch {
          reject(new Error("invalid upload response"));
        }
      } else if (xhr.status === 409) {
        const data = parseJson(xhr);
        if (isDuplicateUploadBody(data)) {
          reject(new DuplicateUploadError(data));
          return;
        }
        reject(new Error(extractErrorDetail(xhr) ?? `upload failed with status ${xhr.status}`));
      } else {
        reject(new Error(extractErrorDetail(xhr) ?? `upload failed with status ${xhr.status}`));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error("network error during upload"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(timedOut ? new Error("upload timed out") : new UploadAbortedError());
    };
    xhr.send(params.file);
  });
};
