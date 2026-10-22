import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CommandPalette } from "@/components/shell/CommandPalette";

// jsdom has no ResizeObserver; cmdk's `Command` uses one internally to size
// its list. A minimal no-op stub is enough for rendering/selection tests.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
// jsdom also has no layout, so `scrollIntoView` (cmdk scrolls the selected
// item into view) is missing entirely.
Element.prototype.scrollIntoView = vi.fn();

vi.mock("@/api/auth", () => ({
  useAuth: () => ({ can: () => true }),
}));

vi.mock("@/api/features", () => ({
  useFeatures: () => ({ data: { printer_enabled: true } }),
}));

vi.mock("@/api/printers", () => ({
  usePrintersOverview: () => ({
    data: {
      printers: [
        { printer: { id: 1, name: "Bambu A1" } },
        { printer: { id: 2, name: "Bambu P1S" } },
      ],
      up_next: [],
      recent_runs: [],
    },
  }),
}));

vi.mock("@/api/collections", () => ({
  useFollowedCollections: () => ({ data: [{ id: 1, title: "MakerWorld favorites" }] }),
}));

vi.mock("@/api/categories", () => ({
  useCategories: () => ({ data: [{ id: 1, name: "Miniatures", color: "#f00", model_count: 3 }] }),
}));

vi.mock("@/api/library", () => ({
  useModelSearchQuery: () => ({ data: undefined }),
}));

function renderPalette(open: boolean) {
  const rootRoute = createRootRoute({
    component: () => <CommandPalette open={open} onOpenChange={() => {}} />,
  });
  const modelRoute = createRoute({ getParentRoute: () => rootRoute, path: "/models/$slug", component: () => null });
  const libraryRoute = createRoute({ getParentRoute: () => rootRoute, path: "/library", component: () => null });
  const printerRoute = createRoute({ getParentRoute: () => rootRoute, path: "/printers/$id", component: () => null });
  const historyRoute = createRoute({ getParentRoute: () => rootRoute, path: "/print/history", component: () => null });
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, libraryRoute, modelRoute, printerRoute, historyRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const result = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router };
}

describe("CommandPalette", () => {
  it("renders Pages and Collections sections when open", async () => {
    renderPalette(true);

    expect(await screen.findByText("Pages")).toBeInTheDocument();
    expect(screen.getByText("Models")).toBeInTheDocument();
    // "Collections" appears twice: the group heading and the Collections
    // page nav item.
    expect(screen.getAllByText("Collections").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("MakerWorld favorites")).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    renderPalette(false);

    expect(screen.queryByText("Pages")).not.toBeInTheDocument();
  });

  it("lists one Printers entry per printer, by name", async () => {
    renderPalette(true);

    expect(await screen.findByText("Printers")).toBeInTheDocument();
    expect(screen.getByText("Bambu A1")).toBeInTheDocument();
    expect(screen.getByText("Bambu P1S")).toBeInTheDocument();
  });

  it("contains no /dashboard reference anywhere in the palette", async () => {
    renderPalette(true);
    await screen.findByText("Pages");

    expect(screen.queryByText(/\/dashboard/i)).not.toBeInTheDocument();
  });

  it("navigates to /library?collection= when a collection is selected", async () => {
    const { router } = renderPalette(true);
    const item = await screen.findByText("MakerWorld favorites");

    fireEvent.click(item);

    expect(router.state.location.pathname).toBe("/library");
    expect(router.state.location.search).toEqual({ collection: 1 });
  });

  it("includes Open print queue and Run history stats actions", async () => {
    renderPalette(true);

    expect(await screen.findByText("Actions")).toBeInTheDocument();
    expect(screen.getByText("Open print queue")).toBeInTheDocument();
    expect(screen.getByText("Run history stats")).toBeInTheDocument();
  });
});
