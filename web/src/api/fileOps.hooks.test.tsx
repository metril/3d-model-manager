import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DELETE_CONCURRENCY, MOVE_CHUNK, MoveStillRunning, useDeleteFiles, useMoveFiles } from "@/api/fileOps";

const { post, get, patch, del } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn(), patch: vi.fn(), del: vi.fn() }));
vi.mock("@/api/client", () => ({ api: { post, get, patch, delete: del } }));

afterEach(() => {
  vi.useRealTimers();
  for (const m of [post, get, patch, del]) m.mockReset();
});

function wrap({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}
const moves = (n: number) => Array.from({ length: n }, (_, i) => ({ file_id: i + 1, rel_path: `d/${i}.stl` }));

describe("useMoveFiles", () => {
  it("splits big batches into sequential chunks of at most MOVE_CHUNK", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    post.mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((r) => setTimeout(r, 5));
      inFlight -= 1;
      return { id: "j", state: "done", error: null };
    });
    const { result } = renderHook(() => useMoveFiles({ id: 7, slug: "m" }), { wrapper: wrap });
    act(() => result.current.mutate(moves(MOVE_CHUNK * 2 + 5)));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(post).toHaveBeenCalledTimes(3);
    expect(post.mock.calls.map((c) => c[1].moves.length)).toEqual([MOVE_CHUNK, MOVE_CHUNK, 5]);
    expect(maxInFlight).toBe(1);
  });

  it("reports a job that outlives the polling window as still running, with the unsubmitted remainder", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    post.mockResolvedValue({ id: "j1", state: "queued", error: null });
    get.mockResolvedValue({ id: "j1", state: "running", error: null });
    const { result } = renderHook(() => useMoveFiles({ id: 7, slug: "m" }), { wrapper: wrap });
    act(() => result.current.mutate(moves(MOVE_CHUNK + 3)));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700 * 125);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(MoveStillRunning);
    expect((result.current.error as MoveStillRunning).remaining).toBe(3);
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("useDeleteFiles", () => {
  it("never has more than DELETE_CONCURRENCY requests in flight and counts failures", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    del.mockImplementation(async (path: string) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((r) => setTimeout(r, 3));
      inFlight -= 1;
      if (path === "/files/5") throw new Error("nope");
    });
    const { result } = renderHook(() => useDeleteFiles("m"), { wrapper: wrap });
    act(() => result.current.mutate(Array.from({ length: 20 }, (_, i) => i + 1)));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(del).toHaveBeenCalledTimes(20);
    expect(maxInFlight).toBeLessThanOrEqual(DELETE_CONCURRENCY);
    expect(maxInFlight).toBeGreaterThan(1);
    expect(result.current.data).toMatchObject({ ok: 19, failed: 1 });
  });
});
