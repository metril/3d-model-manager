import { afterEach, expect, it, vi } from "vitest";
import { parseGcodeAsync } from "./gcodeParseAsync";

afterEach(() => vi.unstubAllGlobals());

it("falls back to the main thread when the worker errors", async () => {
  const terminate = vi.fn();
  class W {
    onmessage: unknown;
    onerror: ((e: { message: string }) => void) | null = null;
    terminate = terminate;
    postMessage() {
      queueMicrotask(() => this.onerror?.({ message: "load failed" }));
    }
  }
  vi.stubGlobal("Worker", W);
  const r = await parseGcodeAsync("M83\nG1 X0 Y0 Z0.2\nG1 X1 E1\n");
  expect(r.segmentCount).toBe(1);
  expect(terminate).toHaveBeenCalled();
});
