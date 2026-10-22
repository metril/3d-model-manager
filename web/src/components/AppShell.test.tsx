import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as UploadPage.test.tsx), so the mutable box the tests write
// to has to be created through `vi.hoisted`.
type ScanRunStub = { id: number; state: string; files_seen: number; files_hashed: number };

const { featuresBox, failedImportsBox, scanRunsBox } = vi.hoisted(() => ({
  featuresBox: { current: { printer_enabled: false } as { printer_enabled: boolean } | undefined },
  failedImportsBox: { current: 0 },
  // R9-D item 7: stands in for `useScanRuns` -- `undefined` means "no runs
  // yet" (chip hidden), same as the real hook's `data` before the first
  // fetch resolves.
  scanRunsBox: { current: undefined as ScanRunStub[] | undefined },
}));

vi.mock("@/api/features", () => ({
  useFeatures: () => ({ data: featuresBox.current, isLoading: false }),
}));

vi.mock("@/api/imports", () => ({
  useFailedImportsCount: () => failedImportsBox.current,
}));

vi.mock("@/api/scan", () => ({
  useScanRuns: () => ({ data: scanRunsBox.current }),
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({ data: { username: "tester" }, can: () => true, canPrinter: () => true }),
  useLogout: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/api/collections", () => ({
  useFollowedCollections: () => ({ data: [] }),
}));

vi.mock("@/api/categories", () => ({
  useCategories: () => ({ data: [] }),
}));

vi.mock("@/api/printers", () => ({
  usePrinters: () => ({ data: [] }),
  usePrinterStatus: () => ({ data: undefined }),
  usePrintersOverview: () => ({ data: undefined }),
}));

vi.mock("@/api/library", () => ({
  useModelSearchQuery: () => ({ data: undefined }),
}));

// AppShell wraps its children in the app-wide SSE `EventsProvider`, which
// normally requires a real `EventSource` (see useEvents.test.tsx) -- nav
// rendering doesn't care about SSE at all, so swap it for a passthrough
// (same pattern as UploadPage.test.tsx mocking `@/hooks/useEvents`).
vi.mock("@/hooks/useEvents", () => ({
  EventsProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

function renderShell() {
  const rootRoute = createRootRoute();
  const shellRoute = createRoute({ getParentRoute: () => rootRoute, id: "shell", component: AppShell });
  const childRoute = createRoute({ getParentRoute: () => shellRoute, path: "/", component: () => <div>home</div> });
  const router = createRouter({
    routeTree: rootRoute.addChildren([shellRoute.addChildren([childRoute])]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("AppShell nav", () => {
  beforeEach(() => {
    featuresBox.current = { printer_enabled: false };
    failedImportsBox.current = 0;
    scanRunsBox.current = undefined;
  });

  it("hides the Print group when the printer feature flag is off", async () => {
    featuresBox.current = { printer_enabled: false };

    renderShell();

    expect(await screen.findByRole("link", { name: "Models" })).toBeInTheDocument();
    expect(screen.queryByText("Print")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Queue" })).not.toBeInTheDocument();
  });

  it("shows the Print group when the printer feature flag is on", async () => {
    featuresBox.current = { printer_enabled: true };

    renderShell();

    expect(await screen.findByText("Print")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Queue" })).toHaveAttribute("href", "/print/queue");
  });

  it("exposes a single global Add action instead of separate Upload/Import nav items", async () => {
    renderShell();

    expect(await screen.findByRole("link", { name: /Add to library/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Upload$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Import$/ })).not.toBeInTheDocument();
  });

  // Saved-collection sync used to be a "Saved" tab behind the "Add to library"
  // button, where no nav label said "collection" or "sync" -- users could not
  // find it at all. It gets a first-class rail entry.
  it("links to the Collections page from the nav rail", async () => {
    renderShell();

    const link = await screen.findByRole("link", { name: /Collections/ });
    expect(link).toHaveAttribute("href", "/collections");
  });

  // Import-health task T3: a failed import is easy to miss on a page nobody
  // is looking at -- the Collections nav entry surfaces a count so it's
  // visible from anywhere in the app.
  it("shows a failed-imports count badge on the Collections nav entry", async () => {
    failedImportsBox.current = 3;

    renderShell();

    const link = await screen.findByRole("link", { name: /Collections/ });
    expect(link).toHaveTextContent("3");
  });

  it("hides the failed-imports badge when there are no failures", async () => {
    failedImportsBox.current = 0;

    renderShell();

    const link = await screen.findByRole("link", { name: /Collections/ });
    expect(link.textContent).toBe("Collections");
  });

  // P3 Task 6: nav is grouped (Print / Library / Inventory) with visible
  // group labels, System (Background jobs, Settings) pinned outside every
  // group.
  it("renders group labels and keeps Settings out of every group", async () => {
    featuresBox.current = { printer_enabled: true };

    renderShell();

    const nav = await screen.findByRole("navigation");
    const groupLabels = within(nav)
      .getAllByText(/^(Print|Library|Inventory)$/)
      .filter((el) => el.tagName === "DIV")
      .map((el) => el.textContent);
    expect(groupLabels).toEqual(["Print", "Library", "Inventory"]);
    expect(screen.getByRole("link", { name: /Settings/ })).toBeInTheDocument();
  });
});

describe("AppShell keyboard shortcuts dialog (R9-C item 5)", () => {
  beforeEach(() => {
    featuresBox.current = { printer_enabled: false };
    failedImportsBox.current = 0;
    scanRunsBox.current = undefined;
  });

  it("`?` opens the keyboard shortcuts dialog", async () => {
    renderShell();
    await screen.findByRole("link", { name: "Models" });

    fireEvent.keyDown(document.body, { key: "?" });

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Keyboard shortcuts")).toBeInTheDocument();
  });

  it("the sidebar's Keyboard shortcuts button opens the same dialog", async () => {
    renderShell();
    await screen.findByRole("link", { name: "Models" });

    fireEvent.click(screen.getByRole("button", { name: "Keyboard shortcuts" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("AppShell scan chip (R9-D item 7)", () => {
  beforeEach(() => {
    featuresBox.current = { printer_enabled: false };
    failedImportsBox.current = 0;
    scanRunsBox.current = undefined;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("hides the chip when there is no scan run", async () => {
    scanRunsBox.current = [];

    renderShell();

    await screen.findByRole("link", { name: "Models" });
    expect(screen.queryByText(/Scanning/)).not.toBeInTheDocument();
    expect(screen.queryByText("Scan done")).not.toBeInTheDocument();
  });

  it("shows the running count while a scan is in flight", async () => {
    scanRunsBox.current = [{ id: 1, state: "running", files_seen: 40, files_hashed: 12 }];

    renderShell();

    expect(await screen.findByText("Scanning… 12/40")).toBeInTheDocument();
  });

  it("shows an indeterminate label before file counts are known", async () => {
    scanRunsBox.current = [{ id: 1, state: "queued", files_seen: 0, files_hashed: 0 }];

    renderShell();

    expect(await screen.findByText("Scanning…")).toBeInTheDocument();
  });

  it("shows 'Scan done' then hides it after ~4s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    scanRunsBox.current = [{ id: 1, state: "done", files_seen: 40, files_hashed: 40 }];

    renderShell();

    expect(await screen.findByText("Scan done")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(4000);
    });

    expect(screen.queryByText("Scan done")).not.toBeInTheDocument();
  });

  it("shows a persistent failure chip linking to Jobs", async () => {
    scanRunsBox.current = [{ id: 1, state: "failed", files_seen: 40, files_hashed: 10 }];

    renderShell();

    expect(await screen.findByText("Scan failed")).toBeInTheDocument();
  });

  it("hides the chip for a skipped run", async () => {
    scanRunsBox.current = [{ id: 1, state: "skipped", files_seen: 0, files_hashed: 0 }];

    renderShell();

    await screen.findByRole("link", { name: "Models" });
    expect(screen.queryByText(/Scan/)).not.toBeInTheDocument();
  });
});
