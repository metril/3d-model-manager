import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadFile } from "@/api/upload";

class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
  onabort?: () => void;
  aborted = false;
  withCredentials = false;
  constructor() {
    FakeXhr.last = this;
  }
  open() {}
  send() {}
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
}

describe("uploadFile idle watchdog", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  const params = { modelId: 1, revisionId: 1, relPath: "a.stl", file: new Blob(["x"]) };

  it("keeps a slow upload alive while progress arrives", async () => {
    const p = uploadFile(params, { timeoutMs: 1000 });
    const settled = vi.fn();
    p.catch(settled);
    for (let i = 0; i < 5; i++) {
      await vi.advanceTimersByTimeAsync(800);
      FakeXhr.last.upload.onprogress?.({ lengthComputable: true, loaded: i, total: 10 });
    }
    expect(FakeXhr.last.aborted).toBe(false);
    expect(settled).not.toHaveBeenCalled();
  });

  it("aborts with a timeout error when progress stalls", async () => {
    const p = uploadFile(params, { timeoutMs: 1000 });
    const assertion = expect(p).rejects.toThrow("upload timed out");
    await vi.advanceTimersByTimeAsync(1100);
    await assertion;
  });
});
