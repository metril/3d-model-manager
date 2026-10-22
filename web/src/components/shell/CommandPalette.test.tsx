import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
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
  const libraryRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([libraryRoute, modelRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
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
});
