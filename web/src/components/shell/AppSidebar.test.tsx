import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppSidebar } from "@/components/shell/AppSidebar";

const { authCanBox } = vi.hoisted(() => ({
  authCanBox: { current: (_p: string) => true as boolean },
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({ data: { username: "tester" }, can: (p: string) => authCanBox.current(p), canPrinter: () => true }),
  useLogout: () => ({ mutate: vi.fn(), isPending: false }),
}));

const { featuresBox, printersBox, financeConfigBox, reviewCountBox } = vi.hoisted(() => ({
  featuresBox: { current: { printer_enabled: false } },
  printersBox: { current: [] as Array<{ id: number; name: string }> },
  financeConfigBox: { current: { billing_enabled: false } as { billing_enabled: boolean } | undefined },
  reviewCountBox: { current: undefined as { count: number } | undefined },
}));

vi.mock("@/api/finance", () => ({
  useFinanceConfig: () => ({ data: financeConfigBox.current }),
}));

vi.mock("@/api/queue", () => ({
  useReviewCount: () => ({ data: reviewCountBox.current }),
}));

vi.mock("@/api/features", () => ({
  useFeatures: () => ({ data: featuresBox.current }),
}));

vi.mock("@/api/printers", () => ({
  usePrinters: (opts?: { enabled?: boolean }) =>
    opts?.enabled === false ? { data: undefined, isLoading: false } : { data: printersBox.current, isLoading: false },
  usePrinterStatus: () => ({ data: undefined }),
}));

const { failedBox } = vi.hoisted(() => ({ failedBox: { current: 0 } }));

vi.mock("@/api/imports", () => ({
  useFailedImportsCount: () => failedBox.current,
}));

vi.mock("@/api/scan", () => ({
  useScanRuns: () => ({ data: undefined }),
}));

const { followedBox } = vi.hoisted(() => ({
  followedBox: { current: [] as Array<{ id: number; title: string }> },
}));

vi.mock("@/api/collections", () => ({
  useFollowedCollections: () => ({ data: followedBox.current }),
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

describe("AppSidebar -- failed-imports badge", () => {
  it("explains the badge and links to the Recent imports tab", async () => {
    failedBox.current = 3;
    renderSidebar();
    const badge = await screen.findByLabelText("3 failed imports \u2014 click to review");
    expect(badge).toHaveAttribute("title", "3 failed imports \u2014 click to review");
    expect(badge.closest("a")).toHaveAttribute("href", "/collections?tab=imports");
    failedBox.current = 0;
  });

  it("has no badge and a plain link when nothing is failed", async () => {
    failedBox.current = 0;
    renderSidebar();
    const link = await screen.findByRole("link", { name: /Collections/ });
    expect(link).toHaveAttribute("href", "/collections");
  });
});

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

  it("does not add per-printer links to the nav", async () => {
    featuresBox.current = { printer_enabled: true };
    printersBox.current = [
      { id: 1, name: "Workshop A1" },
      { id: 2, name: "Garage X1" },
    ];
    renderSidebar();

    await screen.findByRole("link", { name: "Models" });
    expect(screen.queryByRole("link", { name: "Workshop A1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Garage X1" })).not.toBeInTheDocument();
  });

  it("drops the whole Print group, including printer links, when the feature is off", async () => {
    featuresBox.current = { printer_enabled: false };
    printersBox.current = [{ id: 1, name: "Workshop A1" }];
    renderSidebar();

    await screen.findByRole("link", { name: "Models" });
    expect(screen.queryByRole("link", { name: "Workshop A1" })).not.toBeInTheDocument();
    expect(screen.queryByText("Print")).not.toBeInTheDocument();
  });

  it("pins Background jobs and Settings at the bottom", async () => {
    renderSidebar();

    expect(await screen.findByRole("link", { name: "Background jobs" })).toHaveAttribute(
      "href",
      "/system/jobs",
    );
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });
});

describe("AppSidebar -- Queue review badge (P7 Task 9)", () => {
  beforeEach(() => {
    featuresBox.current = { printer_enabled: true };
    printersBox.current = [];
    reviewCountBox.current = undefined;
  });

  it("shows the review count on the Queue nav item with its own aria-label", async () => {
    reviewCountBox.current = { count: 3 };
    renderSidebar();

    const badge = await screen.findByLabelText("3 awaiting review");
    expect(badge).toHaveTextContent("3");
  });

  it("shows no badge when the count is zero", async () => {
    reviewCountBox.current = { count: 0 };
    renderSidebar();
    await screen.findByRole("link", { name: "Queue" });
    expect(screen.queryByLabelText(/awaiting review/)).not.toBeInTheDocument();
  });
});

describe("AppSidebar -- Finance nav entry (P7 Task 8)", () => {
  beforeEach(() => {
    authCanBox.current = () => true;
    financeConfigBox.current = { billing_enabled: false };
    reviewCountBox.current = undefined;
  });

  it("shows Finance for a billing manager even when billing is off", async () => {
    renderSidebar();
    expect(await screen.findByRole("link", { name: "Finance" })).toHaveAttribute("href", "/system/finance");
  });

  it("hides Finance for a non-manager when billing is off", async () => {
    authCanBox.current = (p: string) => p !== "billing:manage";
    renderSidebar();
    await screen.findByRole("link", { name: "Settings" });
    expect(screen.queryByRole("link", { name: "Finance" })).not.toBeInTheDocument();
  });

  it("shows Finance for a non-manager once billing is on", async () => {
    authCanBox.current = (p: string) => p !== "billing:manage";
    financeConfigBox.current = { billing_enabled: true };
    renderSidebar();
    expect(await screen.findByRole("link", { name: "Finance" })).toHaveAttribute("href", "/system/finance");
  });
});

describe("AppSidebar -- collapsible sections", () => {
  function renderAt(path: string) {
    const rootRoute = createRootRoute({ component: () => <AppSidebar onOpenShortcuts={vi.fn()} mobileOpen={false} onCloseMobile={vi.fn()} /> });
    const routes = ["/", "/settings", "/library", "/collections"].map((p) =>
      createRoute({ getParentRoute: () => rootRoute, path: p, component: () => null }),
    );
    const router = createRouter({
      routeTree: rootRoute.addChildren(routes),
      history: createMemoryHistory({ initialEntries: [path] }),
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }

  beforeEach(() => {
    window.localStorage.clear();
    featuresBox.current = { printer_enabled: false };
    categoriesBox.current = [{ id: 1, name: "Miniatures", color: "red", model_count: 5 }];
    followedBox.current = [{ id: 7, title: "Dragons" }];
    reviewCountBox.current = undefined;
    failedBox.current = 0;
  });

  it("toggles a group with aria-expanded and persists it", async () => {
    renderAt("/");
    const header = await screen.findByRole("button", { name: /Library/ });
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(header).toHaveAttribute("aria-controls");
    expect(screen.getByRole("link", { name: "Projects" })).toBeInTheDocument();

    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: "Projects" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("tdmm.sidebarSection.library")).toBe("1");

    fireEvent.click(header);
    expect(screen.getByRole("link", { name: "Projects" })).toBeInTheDocument();
    expect(window.localStorage.getItem("tdmm.sidebarSection.library")).toBe("0");
  });

  it("restores the persisted state on mount and syncs across tabs", async () => {
    window.localStorage.setItem("tdmm.sidebarSection.categories", "1");
    renderAt("/");
    const header = await screen.findByRole("button", { name: /Categories/ });
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: /Miniatures/ })).not.toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "tdmm.sidebarSection.categories", newValue: "0" }));
    });
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: /Miniatures/ })).toBeInTheDocument();
  });

  it("collapses the System section", async () => {
    renderAt("/");
    const system = await screen.findByRole("button", { name: /System/ });
    fireEvent.click(system);
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("collapses the collections sublist but keeps the Collections link", async () => {
    renderAt("/");
    const toggle = await screen.findByRole("button", { name: "Hide followed collections" });
    expect(screen.getByRole("link", { name: "Dragons" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All collections" })).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByRole("link", { name: "Dragons" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Collections/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show followed collections" })).toHaveAttribute("aria-expanded", "false");
    expect(window.localStorage.getItem("tdmm.sidebarSection.collections")).toBe("1");
  });

  it("auto-expands a collapsed section when navigating into it", async () => {
    window.localStorage.setItem("tdmm.sidebarSection.system", "1");
    window.localStorage.setItem("tdmm.sidebarSection.library", "1");
    const router = renderAt("/");
    const system = await screen.findByRole("button", { name: /System/ });
    expect(system).toHaveAttribute("aria-expanded", "false");
    expect(system).not.toHaveTextContent("current page");

    await act(async () => {
      await router.navigate({ to: "/settings" });
    });
    expect(screen.getByRole("button", { name: /System/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    // Unrelated collapsed sections stay collapsed.
    expect(screen.getByRole("button", { name: /Library/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("does not re-open a collapsed section on mount, even when the route is inside it", async () => {
    window.localStorage.setItem("tdmm.sidebarSection.system", "1");
    renderAt("/settings");
    const system = await screen.findByRole("button", { name: /System/ });
    expect(system).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps the failed-imports badge visible beside the collections toggle", async () => {
    failedBox.current = 3;
    renderAt("/");
    const badge = await screen.findByLabelText(/3 failed imports/);
    const toggle = screen.getByRole("button", { name: "Hide followed collections" });
    expect(badge.closest("a")).toBeInTheDocument();
    expect(badge.closest("a")?.contains(toggle)).toBe(false);
    fireEvent.click(toggle);
    expect(screen.getByLabelText(/3 failed imports/)).toBeInTheDocument();
  });

  it("labels the active marker on a collapsed collections toggle", async () => {
    window.localStorage.setItem("tdmm.sidebarSection.collections", "1");
    renderAt("/library?collection=7");
    const toggle = await screen.findByRole("button", { name: "Show followed collections" });
    expect(toggle).toHaveTextContent("current page is in this section");
  });

  it("shows the active marker on a collapsed section while the route is inside it", async () => {
    renderAt("/settings");
    const system = await screen.findByRole("button", { name: /System/ });
    fireEvent.click(system);
    expect(system).toHaveAttribute("aria-expanded", "false");
    expect(system).toHaveTextContent("current page is in this section");
  });
});
