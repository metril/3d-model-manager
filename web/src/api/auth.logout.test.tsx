import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useLogout } from "@/api/auth";
import { shouldRetry } from "@/queryClient";
import { ApiError } from "@/api/client";

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: vi.fn().mockResolvedValue(undefined) } };
});

describe("useLogout", () => {
  it("clears the whole query cache on success", async () => {
    const qc = new QueryClient();
    qc.setQueryData(["auth", "me"], { id: 1 });
    qc.setQueryData(["models", "list"], [1, 2]);
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    const { result } = renderHook(() => useLogout(), { wrapper });
    act(() => result.current.mutate());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
  });
});

describe("shouldRetry", () => {
  it("retries network errors and 5xx up to twice, never 4xx", () => {
    expect(shouldRetry(0, new TypeError("Failed to fetch"))).toBe(true);
    expect(shouldRetry(1, new ApiError(503, "x"))).toBe(true);
    expect(shouldRetry(2, new ApiError(503, "x"))).toBe(false);
    expect(shouldRetry(0, new ApiError(404, "x"))).toBe(false);
    expect(shouldRetry(0, new ApiError(401, "x"))).toBe(false);
  });
});
