import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import type { ImportOut } from "@/api/types";
import {
  useDismissFailedImports,
  useDismissImport,
  useFailedImportsCount,
  useImportsList,
  useRetryImport,
} from "@/api/imports";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as jobs.test.tsx), so the fakes have to be created through
// `vi.hoisted`.
const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock },
  };
});

function fakeImport(overrides: Partial<ImportOut> = {}): ImportOut {
  return {
    id: 1,
    url: "https://makerworld.com/en/models/1",
    site: "makerworld",
    external_id: "1",
    state: "done",
    model_id: 5,
    error: null,
    meta: null,
    dismissed_at: null,
    created_at: "2026-07-11T00:00:00Z",
    updated_at: "2026-07-11T00:00:00Z",
    ...overrides,
  };
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useImportsList", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("fetches GET /imports with limit/offset and flattens pages", async () => {
    getMock.mockResolvedValue([fakeImport()]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useImportsList(), { wrapper: wrapper(queryClient) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getMock).toHaveBeenCalledWith("/imports?limit=50&offset=0");
    expect(result.current.data).toEqual([fakeImport()]);
    expect(result.current.hasNextPage).toBe(false);
  });

  it("the failed view adds state=failed and pages by offset", async () => {
    const full = Array.from({ length: 50 }, (_, i) => fakeImport({ id: i + 1, state: "failed" }));
    getMock.mockResolvedValueOnce(full).mockResolvedValueOnce([fakeImport({ id: 99, state: "failed" })]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useImportsList("failed"), { wrapper: wrapper(queryClient) });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    expect(getMock).toHaveBeenCalledWith("/imports?limit=50&offset=0&state=failed");
    await result.current.fetchNextPage();
    await waitFor(() => expect(result.current.data).toHaveLength(51));
    expect(getMock).toHaveBeenLastCalledWith("/imports?limit=50&offset=50&state=failed");
  });
});

describe("useFailedImportsCount", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("reads GET /imports/failed-count", async () => {
    getMock.mockResolvedValue({ count: 7 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useFailedImportsCount(), { wrapper: wrapper(queryClient) });

    await waitFor(() => expect(result.current).toBe(7));
    expect(getMock).toHaveBeenCalledWith("/imports/failed-count");
  });

  it("reports 0 before the query has resolved", () => {
    getMock.mockReturnValue(new Promise(() => {})); // never resolves
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useFailedImportsCount(), { wrapper: wrapper(queryClient) });

    expect(result.current).toBe(0);
  });
});

describe("dismiss mutations", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("dismiss posts to /imports/{id}/dismiss and invalidates imports", async () => {
    postMock.mockResolvedValue(fakeImport({ id: 7, state: "failed" }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useDismissImport(), { wrapper: wrapper(queryClient) });
    result.current.mutate(7);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(postMock).toHaveBeenCalledWith("/imports/7/dismiss");
    expect(spy).toHaveBeenCalledWith({ queryKey: ["imports"] });
  });

  it("dismiss-all posts to /imports/dismiss-failed and invalidates imports", async () => {
    postMock.mockResolvedValue({ dismissed: 2 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useDismissFailedImports(), { wrapper: wrapper(queryClient) });
    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(postMock).toHaveBeenCalledWith("/imports/dismiss-failed");
    expect(spy).toHaveBeenCalledWith({ queryKey: ["imports"] });
  });
});

describe("useRetryImport", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("posts to /imports/{id}/retry and invalidates imports + models on success", async () => {
    postMock.mockResolvedValue(fakeImport({ id: 7, state: "pending" }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useRetryImport(), { wrapper: wrapper(queryClient) });

    result.current.mutate(7);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(postMock).toHaveBeenCalledWith("/imports/7/retry");
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["imports"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["models"] });
  });

  it("surfaces the backend's 409 detail as an ApiError on failure", async () => {
    const { ApiError } = await import("@/api/client");
    postMock.mockRejectedValue(new ApiError(409, "import 7 is not failed"));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useRetryImport(), { wrapper: wrapper(queryClient) });

    result.current.mutate(7);

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as InstanceType<typeof ApiError>).detail).toBe("import 7 is not failed");
  });
});
