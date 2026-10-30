import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMoveFolder } from "@/api/storageMove";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("@/api/client", () => ({ api: { post, get } }));

afterEach(() => vi.useRealTimers());

function wrap({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("useMoveFolder", () => {
  it("polls the job by id and errors if it never finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    post.mockResolvedValue({ id: "j1", state: "queued", error: null });
    get.mockResolvedValue({ id: "j1", state: "running", error: null });
    const { result } = renderHook(() => useMoveFolder(), { wrapper: wrap });
    act(() => result.current.mutate({ src: "a/b/c", dest: "a/b/d" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700 * 125);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(get).toHaveBeenCalledWith("/jobs/j1");
    expect(result.current.error?.message).toMatch(/too long/);
  });

  it("surfaces a failed job", async () => {
    post.mockResolvedValue({ id: "j2", state: "failed", error: "hash mismatch" });
    const { result } = renderHook(() => useMoveFolder(), { wrapper: wrap });
    act(() => result.current.mutate({ src: "a/b/c", dest: "a/b/d" }));
    await waitFor(() => expect(result.current.error?.message).toBe("hash mismatch"));
  });
});
