import { parseGcodeToolpath, type ParsedToolpath } from "./gcodeToolpath";

/** Parse in a module Web Worker; falls back to the main thread when Workers are unavailable (jsdom, old browsers). */
export function parseGcodeAsync(text: string, signal?: AbortSignal): Promise<ParsedToolpath> {
  if (typeof Worker === "undefined") return Promise.resolve().then(() => parseGcodeToolpath(text));
  return new Promise((resolve, reject) => {
    let worker: Worker;
    try {
      worker = new Worker(new URL("./gcodeToolpath.worker.ts", import.meta.url), { type: "module" });
    } catch {
      resolve(parseGcodeToolpath(text));
      return;
    }
    const done = () => worker.terminate();
    signal?.addEventListener(
      "abort",
      () => {
        done();
        reject(new DOMException("aborted", "AbortError"));
      },
      { once: true },
    );
    worker.onmessage = (e: MessageEvent<{ ok: boolean; result?: ParsedToolpath; error?: string }>) => {
      done();
      if (e.data.ok && e.data.result) resolve(e.data.result);
      else reject(new Error(e.data.error ?? "parse failed"));
    };
    worker.onerror = () => {
      // Worker failed to load (CSP, stale chunk): parse on the main thread instead.
      done();
      try {
        resolve(parseGcodeToolpath(text));
      } catch (err) {
        reject(err);
      }
    };
    worker.postMessage(text);
  });
}
