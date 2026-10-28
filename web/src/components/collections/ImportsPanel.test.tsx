import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ImportsPanel } from "@/components/collections/ImportsPanel";
import type { ImportOut } from "@/api/types";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as SavedPanel.test.tsx), so the mutable boxes the tests
// write to have to be created through `vi.hoisted`.
const { importsBox, retryMock, dismissMock, dismissAllMock, failedBox, filterSpy, moreMock } = vi.hoisted(() => ({
  importsBox: {
    current: { data: [] as ImportOut[], isLoading: false } as Record<string, unknown>,
  },
  failedBox: { current: 0 },
  filterSpy: vi.fn(),
  moreMock: vi.fn(),
  retryMock: vi.fn(),
  dismissMock: vi.fn(),
  dismissAllMock: vi.fn(),
}));

vi.mock("@/api/imports", () => ({
  useImportsList: (f: string) => {
    filterSpy(f);
    return { fetchNextPage: moreMock, hasNextPage: false, isFetchingNextPage: false, ...importsBox.current };
  },
  useFailedImportsCount: () => failedBox.current,
  useRetryImport: () => ({ mutate: retryMock, isPending: false }),
  useDismissImport: () => ({ mutate: dismissMock, isPending: false }),
  useDismissFailedImports: () => ({ mutate: dismissAllMock, isPending: false }),
}));

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
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ImportsPanel />
    </QueryClientProvider>,
  );
}

describe("ImportsPanel", () => {
  beforeEach(() => {
    retryMock.mockReset();
    dismissMock.mockReset();
    dismissAllMock.mockReset();
    moreMock.mockReset();
    filterSpy.mockReset();
    failedBox.current = 0;
    importsBox.current = { data: [], isLoading: false };
  });

  it("shows the empty state when there are no imports", () => {
    renderPanel();

    expect(screen.getByText("Recent imports")).toBeInTheDocument();
    expect(
      screen.getByText("No imports yet. Save a model from the extension or approve one from a collection review."),
    ).toBeInTheDocument();
  });

  it("renders each row's state badge, title, and relative time", () => {
    importsBox.current = {
      data: [
        fakeImport({ id: 1, state: "done", url: "https://makerworld.com/en/models/1" }),
        fakeImport({ id: 2, state: "pending", url: "https://www.thingiverse.com/thing:2" }),
        fakeImport({ id: 3, state: "downloading", url: "https://www.printables.com/model/3" }),
      ],
      isLoading: false,
    };

    renderPanel();

    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("Downloading")).toBeInTheDocument();
    // No meta.title on any row -- falls back to the URL.
    expect(screen.getByText("https://makerworld.com/en/models/1")).toBeInTheDocument();
  });

  it("gives a failed row the destructive badge variant, its error text, and a Retry button", () => {
    importsBox.current = {
      data: [fakeImport({ id: 9, state: "failed", error: "Bambu session expired" })],
      isLoading: false,
    };

    renderPanel();

    const badge = screen.getAllByText("Failed").find((el) => el.getAttribute("data-slot") === "badge")!;
    expect(badge).toHaveAttribute("data-variant", "destructive");
    expect(screen.getByText("Bambu session expired")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("POSTs to /imports/{id}/retry when Retry is clicked", () => {
    importsBox.current = {
      data: [fakeImport({ id: 9, state: "failed", error: "boom" })],
      isLoading: false,
    };

    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(retryMock).toHaveBeenCalledWith(9);
  });

  it("Dismiss calls the dismiss mutation; Dismiss all failed shows the undismissed count", () => {
    importsBox.current = {
      data: [
        fakeImport({ id: 1, state: "failed", error: "a" }),
        fakeImport({ id: 2, state: "failed", error: "b" }),
        fakeImport({ id: 3, state: "failed", error: "c", dismissed_at: new Date().toISOString() }),
      ],
      isLoading: false,
    };

    failedBox.current = 2;
    renderPanel();
    // Row 3 is dismissed: no Dismiss button for it, but a label, and Retry stays.
    expect(screen.getAllByRole("button", { name: "Dismiss" })).toHaveLength(2);
    expect(screen.getByText("Dismissed")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(3);

    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
    expect(dismissMock).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss all failed (2)" }));
    expect(dismissAllMock).toHaveBeenCalled();
  });

  it("hides Dismiss all failed when nothing is undismissed", () => {
    importsBox.current = { data: [fakeImport({ id: 1 })], isLoading: false };
    renderPanel();
    expect(screen.queryByRole("button", { name: /Dismiss all failed/ })).not.toBeInTheDocument();
  });

  it("lists every returned row (no client-side cap) and pages with Show more", () => {
    importsBox.current = {
      data: Array.from({ length: 13 }, (_, i) => fakeImport({ id: i + 1, url: `https://example.com/${i + 1}` })),
      isLoading: false,
      hasNextPage: true,
    };

    renderPanel();

    expect(screen.getAllByText("Done")).toHaveLength(13);
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(moreMock).toHaveBeenCalled();
  });

  it("the Failed filter switches the query to the failed view", () => {
    renderPanel();
    expect(filterSpy).toHaveBeenLastCalledWith("all");
    fireEvent.click(screen.getByRole("button", { name: "Failed" }));
    expect(filterSpy).toHaveBeenLastCalledWith("failed");
    expect(screen.getByText("No failed imports.")).toBeInTheDocument();
  });

  it("wraps long URLs instead of truncating and keeps the full URL in the title", () => {
    const url = "https://example.com/" + "a".repeat(200);
    importsBox.current = { data: [fakeImport({ url })], isLoading: false };
    renderPanel();
    const el = screen.getByTitle(url);
    expect(el.className).toContain("break-all");
    expect(el.className).not.toContain("truncate");
  });
});
