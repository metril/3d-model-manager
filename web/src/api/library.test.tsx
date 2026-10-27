import { QueryClient, QueryClientProvider, type InfiniteData } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import type { GalleryPage, ModelDetail, ModelSummary } from "@/api/types";
import { buildModelsUrl, modelQueryOptions, patchModelInListCache, useArchiveModel, usePatchModel } from "@/api/library";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as jobs.test.tsx), so the fakes have to be created through
// `vi.hoisted`.
const { getMock, patchMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  patchMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, patch: patchMock },
  };
});

const LIST_KEY = ["models", "list"] as const;

function fakeModel(overrides: Partial<ModelSummary> = {}): ModelSummary {
  return {
    id: 1,
    slug: "gizmo",
    name: "Gizmo",
    description: null,
    tags: [],
    updated_at: "2026-07-05T00:00:00Z",
    created_at: "2026-07-05T00:00:00Z",
    file_count: 1,
    formats: [],
    cover: null,
    render_url: null,
    print_time_s: null,
    has_sliced: false,
    source_site: null,
    review_state: null,
    source_collection_id: null,
    source_collection_title: null,
    favorite: false,
    dims_mm: null,
    best_slicer_file: null,
    printable_file: null,
    ...overrides,
  };
}

function fakeDetail(overrides: Partial<ModelDetail> = {}): ModelDetail {
  return {
    id: 1,
    slug: "gizmo",
    name: "Gizmo",
    description: null,
    source_url: null,
    source_site: null,
    source_author: null,
    source_license: null,
    source_collection_id: null,
    source_collection_title: null,
    imported_at: null,
    cover_blob_hash: null,
    is_archived: false,
    created_at: "2026-07-05T00:00:00Z",
    updated_at: "2026-07-05T00:00:00Z",
    tags: [],
    current_revision: null,
    notes: [],
    review_state: null,
    backends: [],
    favorite: false,
    print_count: 0,
    last_printed_at: null,
    metadata: null,
    print_tips: null,
    ...overrides,
  };
}

function page(items: ModelSummary[], next_cursor: string | null = null): GalleryPage {
  return { items, next_cursor };
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function seedListCache(
  queryClient: QueryClient,
  queryKey: readonly unknown[],
  pages: GalleryPage[],
): void {
  queryClient.setQueryData<InfiniteData<GalleryPage>>(queryKey, {
    pages,
    pageParams: pages.map((_, i) => (i === 0 ? undefined : `cursor-${i}`)),
  });
}

describe("patchModelInListCache", () => {
  it("patches only the matching item, leaving other pages referentially equal (structural sharing)", () => {
    const queryClient = new QueryClient();
    const queryKey = [...LIST_KEY, { sort: "name" }];
    const pageOne = page([fakeModel({ id: 1, slug: "a" }), fakeModel({ id: 2, slug: "b" })]);
    const pageTwo = page([fakeModel({ id: 3, slug: "c" }), fakeModel({ id: 4, slug: "target" })]);
    const pageThree = page([fakeModel({ id: 5, slug: "e" })]);
    seedListCache(queryClient, queryKey, [pageOne, pageTwo, pageThree]);

    patchModelInListCache(queryClient, LIST_KEY, (item) =>
      item.slug === "target" ? { ...item, favorite: true } : undefined,
    );

    const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
    expect(data).toBeDefined();
    expect(data!.pages[0]).toBe(pageOne);
    expect(data!.pages[2]).toBe(pageThree);
    expect(data!.pages[1]).not.toBe(pageTwo);
    expect(data!.pages[1].items[0]).toBe(pageTwo.items[0]);
    expect(data!.pages[1].items[1].favorite).toBe(true);
    expect(data!.pages[1].items[1]).not.toBe(pageTwo.items[1]);
  });

  it("removes an item from its page when the updater returns null", () => {
    const queryClient = new QueryClient();
    const queryKey = [...LIST_KEY, { sort: "name" }];
    seedListCache(queryClient, queryKey, [
      page([fakeModel({ id: 1, slug: "a" }), fakeModel({ id: 2, slug: "b" })]),
    ]);

    patchModelInListCache(queryClient, LIST_KEY, (item) => (item.slug === "b" ? null : undefined));

    const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
    expect(data!.pages[0].items).toHaveLength(1);
    expect(data!.pages[0].items[0].slug).toBe("a");
  });

  it("leaves unmatched list queries untouched", () => {
    const queryClient = new QueryClient();
    const queryKey = [...LIST_KEY, { sort: "name" }];
    const original = page([fakeModel({ id: 1, slug: "a" })]);
    seedListCache(queryClient, queryKey, [original]);

    patchModelInListCache(queryClient, LIST_KEY, () => undefined);

    const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
    expect(data!.pages[0]).toBe(original);
  });
});

describe("usePatchModel", () => {
  beforeEach(() => {
    getMock.mockReset();
    patchMock.mockReset();
  });

  it("updates the list cache optimistically before the request resolves, and rolls back on failure", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryKey = [...LIST_KEY, { sort: "name" }];
    seedListCache(queryClient, queryKey, [page([fakeModel({ id: 1, slug: "gizmo", favorite: false })])]);

    let resolveRequest!: (value: ModelDetail) => void;
    patchMock.mockReturnValue(
      new Promise<ModelDetail>((resolve) => {
        resolveRequest = resolve;
      }),
    );

    const { result } = renderHook(() => usePatchModel("gizmo"), { wrapper: wrapper(queryClient) });
    result.current.mutate({ favorite: true });

    await waitFor(() => {
      const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
      expect(data!.pages[0].items[0].favorite).toBe(true);
    });
    expect(result.current.isSuccess).toBe(false);

    resolveRequest(fakeDetail({ favorite: true }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("rolls back the list cache to its snapshot when the request is rejected", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryKey = [...LIST_KEY, { sort: "name" }];
    seedListCache(queryClient, queryKey, [page([fakeModel({ id: 1, slug: "gizmo", favorite: false })])]);
    let rejectRequest!: (error: Error) => void;
    patchMock.mockReturnValue(
      new Promise<ModelDetail>((_resolve, reject) => {
        rejectRequest = reject;
      }),
    );

    const { result } = renderHook(() => usePatchModel("gizmo"), { wrapper: wrapper(queryClient) });
    result.current.mutate({ favorite: true });

    await waitFor(() => {
      const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
      expect(data!.pages[0].items[0].favorite).toBe(true);
    });

    rejectRequest(new Error("boom"));
    await waitFor(() => expect(result.current.isError).toBe(true));
    const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
    expect(data!.pages[0].items[0].favorite).toBe(false);
  });

  it("does not invalidate the list query for a single-field patch", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryKey = [...LIST_KEY, { sort: "name" }];
    seedListCache(queryClient, queryKey, [page([fakeModel({ id: 1, slug: "gizmo", favorite: false })])]);
    patchMock.mockResolvedValue(fakeDetail({ favorite: true }));
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => usePatchModel("gizmo"), { wrapper: wrapper(queryClient) });
    result.current.mutate({ favorite: true });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const listInvalidations = invalidateSpy.mock.calls.filter(
      ([opts]) => Array.isArray(opts?.queryKey) && opts.queryKey[0] === "models" && opts.queryKey[1] === "list",
    );
    expect(listInvalidations).toHaveLength(0);
  });
});

describe("useArchiveModel", () => {
  beforeEach(() => {
    getMock.mockReset();
    patchMock.mockReset();
  });

  it("optimistically removes the model from the list cache when archiving", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryKey = [...LIST_KEY, { sort: "name" }];
    seedListCache(queryClient, queryKey, [
      page([fakeModel({ id: 1, slug: "gizmo" }), fakeModel({ id: 2, slug: "other" })]),
    ]);
    patchMock.mockResolvedValue(fakeDetail({ is_archived: true }));

    const { result } = renderHook(() => useArchiveModel("gizmo"), { wrapper: wrapper(queryClient) });
    result.current.mutate(true);

    await waitFor(() => {
      const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
      expect(data!.pages[0].items).toHaveLength(1);
      expect(data!.pages[0].items[0].slug).toBe("other");
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("restores the removed item on a rejected archive request", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryKey = [...LIST_KEY, { sort: "name" }];
    seedListCache(queryClient, queryKey, [page([fakeModel({ id: 1, slug: "gizmo" })])]);
    let rejectRequest!: (error: Error) => void;
    patchMock.mockReturnValue(
      new Promise<ModelDetail>((_resolve, reject) => {
        rejectRequest = reject;
      }),
    );

    const { result } = renderHook(() => useArchiveModel("gizmo"), { wrapper: wrapper(queryClient) });
    result.current.mutate(true);

    await waitFor(() => {
      const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
      expect(data!.pages[0].items).toHaveLength(0);
    });

    rejectRequest(new Error("boom"));
    await waitFor(() => expect(result.current.isError).toBe(true));
    const data = queryClient.getQueryData<InfiniteData<GalleryPage>>(queryKey);
    expect(data!.pages[0].items).toHaveLength(1);
    expect(data!.pages[0].items[0].slug).toBe("gizmo");
  });
});

// Sanity check that `modelQueryOptions` (the detail query key builder used
// throughout library.ts) still matches what the mutations above key off of.
describe("modelQueryOptions", () => {
  it("keys the detail query by slug", () => {
    expect(modelQueryOptions("gizmo").queryKey).toEqual(["models", "detail", "gizmo"]);
  });
});

describe("buildModelsUrl tag modes", () => {
  it("sends tag_mode and file_tag_mode independently", () => {
    const url = buildModelsUrl({ tags: [1, 2], tagMode: "any", fileTags: [3, 4], fileTagMode: "all", sort: "new" });
    const p = new URL(url, "http://x").searchParams;
    expect(p.get("tag_mode")).toBeNull();
    expect(p.get("file_tag_mode")).toBe("all");
    const url2 = buildModelsUrl({ tags: [1, 2], tagMode: "all", fileTags: [3, 4], sort: "new" });
    const p2 = new URL(url2, "http://x").searchParams;
    expect(p2.get("tag_mode")).toBe("all");
    expect(p2.get("file_tag_mode")).toBeNull();
  });
});
