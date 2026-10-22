import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppSidebar } from "@/components/shell/AppSidebar";

vi.mock("@/api/auth", () => ({
  useAuth: () => ({ data: { username: "tester" }, can: () => true, canPrinter: () => true }),
  useLogout: () => ({ mutate: vi.fn(), isPending: false }),
}));

const { featuresBox, printersBox } = vi.hoisted(() => ({
  featuresBox: { current: { printer_enabled: false } },
  printersBox: { current: [] as Array<{ id: number; name: string }> },
}));

vi.mock("@/api/features", () => ({
  useFeatures: () => ({ data: featuresBox.current }),
}));

vi.mock("@/api/printers", () => ({
  usePrinters: (opts?: { enabled?: boolean }) =>
    opts?.enabled === false ? { data: undefined, isLoading: false } : { data: printersBox.current, isLoading: false },
  usePrinterStatus: () => ({ data: undefined }),
}));

vi.mock("@/api/imports", () => ({
  useFailedImportsCount: () => 0,
}));

vi.mock("@/api/scan", () => ({
  useScanRuns: () => ({ data: undefined }),
}));

vi.mock("@/api/collections", () => ({
  useFollowedCollections: () => ({ data: [] }),
}));

const { categoriesBox } = vi.hoisted(() => ({
  categoriesBox: { current: [] as Array<{ id: number; name: string; color: string | null; model_count: number }> },
}));

function renderSidebarAt(initialEntries: string[]) {
  const rootRoute = createRootRoute({ component: () => <AppSidebar onOpenShortcuts={vi.fn()} mobileOpen={false} onCloseMobile={vi.fn()} /> });
  const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute]),
    history: createMemoryHistory({ initialEntries }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

vi.mock("@/api/categories", () => ({
  useCategories: () => ({ data: categoriesBox.current }),
}));

function renderSidebar() {
  const rootRoute = createRootRoute({ component: () => <AppSidebar onOpenShortcuts={vi.fn()} mobileOpen={false} onCloseMobile={vi.fn()} /> });
  const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => null });
  const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/settings", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute, settingsRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("AppSidebar -- Categories (R13b)", () => {
  it("renders no Categories section when there are no categories", async () => {
    categoriesBox.current = [];
    renderSidebar();

    await screen.findByRole("link", { name: "Models" });
    expect(screen.queryByText("Categories")).not.toBeInTheDocument();
  });

  it("lists each category with its color dot and model count, linking to ?category=<id>", async () => {
    categoriesBox.current = [
      { id: 1, name: "Miniatures", color: "red", model_count: 5 },
      { id: 2, name: "Vases", color: null, model_count: 0 },
    ];
    renderSidebar();

    expect(await screen.findByText("Categories")).toBeInTheDocument();
    const miniLink = screen.getByRole("link", { name: /Miniatures/ });
    expect(miniLink).toHaveAttribute("href", "/library?category=1");
    expect(miniLink).toHaveTextContent("5");

    const vasesLink = screen.getByRole("link", { name: /Vases/ });
    expect(vasesLink).toHaveAttribute("href", "/library?category=2");
  });

  // Fix-review finding 3: the category link used to write a brand-new
  // search object (`search={{ category: id }}`), dropping every other
  // param (e.g. `?path=` from the folder view) instead of merging into it.
  it("preserves other search params (e.g. path) when building a category link", async () => {
    categoriesBox.current = [{ id: 1, name: "Miniatures", color: "red", model_count: 5 }];
    renderSidebarAt(["/?path=figures"]);

    const miniLink = await screen.findByRole("link", { name: /Miniatures/ });
    const href = miniLink.getAttribute("href") ?? "";
    const params = new URLSearchParams(href.split("?")[1]);
    expect(params.get("path")).toBe("figures");
    expect(params.get("category")).toBe("1");
  });
});

describe("AppSidebar -- Print group (P3 Task 6)", () => {
  beforeEach(() => {
    featuresBox.current = { printer_enabled: false };
    printersBox.current = [];
  });

  it("links to each configured printer's detail page when the feature is on", async () => {
    featuresBox.current = { printer_enabled: true };
    printersBox.current = [
      { id: 1, name: "Workshop A1" },
      { id: 2, name: "Garage X1" },
    ];
    renderSidebar();

    const a1 = await screen.findByRole("link", { name: "Workshop A1" });
    expect(a1).toHaveAttribute("href", "/printers/1");
    expect(screen.getByRole("link", { name: "Garage X1" })).toHaveAttribute("href", "/printers/2");
  });

  // Fix-review finding 1: every per-printer item shares the same `to`
  // (`/printers/$id`), so keying solely off `item.to` collided across
  // printers -- React logs a duplicate-key warning and can misrender the
  // list on reorder/update. Keying off `to` + the printer id must render
  // two distinct links with no such warning.
  it("renders two distinct printer links with no duplicate-key warning", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    featuresBox.current = { printer_enabled: true };
    printersBox.current = [
      { id: 1, name: "Workshop A1" },
      { id: 2, name: "Garage X1" },
    ];
    renderSidebar();

    expect(await screen.findByRole("link", { name: "Workshop A1" })).toHaveAttribute("href", "/printers/1");
    expect(screen.getByRole("link", { name: "Garage X1" })).toHaveAttribute("href", "/printers/2");
    expect(errorSpy.mock.calls.some((args) => String(args[0]).includes("same key"))).toBe(false);
    errorSpy.mockRestore();
  });

  it("drops the whole Print group, including printer links, when the feature is off", async () => {
    featuresBox.current = { printer_enabled: false };
    printersBox.current = [{ id: 1, name: "Workshop A1" }];
    renderSidebar();

    await screen.findByRole("link", { name: "Models" });
    expect(screen.queryByRole("link", { name: "Workshop A1" })).not.toBeInTheDocument();
    expect(screen.queryByText("Print")).not.toBeInTheDocument();
  });

  it("pins Background jobs and Settings at the bottom, with no Finance link", async () => {
    renderSidebar();

    expect(await screen.findByRole("link", { name: "Background jobs" })).toHaveAttribute(
      "href",
      "/system/jobs",
    );
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("link", { name: "Finance" })).not.toBeInTheDocument();
  });
});
