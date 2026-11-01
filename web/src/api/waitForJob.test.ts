import { afterEach, describe, expect, it, vi } from "vitest";

import { waitForJob } from "@/api/waitForJob";
import type { JobOut } from "@/api/types";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/api/client", () => ({ api: { get } }));

const job = (state: string, error: string | null = null) => ({ id: "j", state, error }) as unknown as JobOut;

afterEach(() => {
  vi.useRealTimers();
  get.mockReset();
});

describe("waitForJob", () => {
  it("resolves immediately for a done job without polling", async () => {
    await expect(waitForJob(job("done"))).resolves.toBeUndefined();
    expect(get).not.toHaveBeenCalled();
  });

  it("polls until done", async () => {
    vi.useFakeTimers();
    get.mockResolvedValueOnce(job("running")).mockResolvedValueOnce(job("done"));
    const p = waitForJob(job("queued"));
    await vi.advanceTimersByTimeAsync(2000);
    await expect(p).resolves.toBeUndefined();
    expect(get).toHaveBeenCalledWith("/jobs/j");
  });

  it("throws the job error, or a labelled fallback", async () => {
    await expect(waitForJob(job("failed", "boom"))).rejects.toThrow("boom");
    await expect(waitForJob(job("dead"), "rename")).rejects.toThrow("rename failed");
  });
});
