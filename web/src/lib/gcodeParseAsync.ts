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
    signal?.addEventListener("abort", () => {
      done();
      reject(new DOMException("aborted", "AbortError"));
    });
    worker.onmessage = (e: MessageEvent<{ ok: boolean; result?: ParsedToolpath; error?: string }>) => {
      done();
      if (e.data.ok && e.data.result) resolve(e.data.result);
      else reject(new Error(e.data.error ?? "parse failed"));
    };
    worker.onerror = (e) => {
      done();
      reject(new Error(e.message || "parse failed"));
    };
    worker.postMessage(text);
  });
}
