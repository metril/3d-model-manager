import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ArchiveOut, StatsOut } from "@/api/types";
import { PrintHistoryPage } from "@/pages/PrintHistoryPage";
import { parsePrintHistorySearch } from "@/pages/printHistorySearch";

// Radix Tabs switches on focus/pointer, not a bare `fireEvent.click`, so tab
// activation is unreliable under jsdom (same limitation CollectionsPage.test
// documents). `PrintHistoryPage`'s tabs are fully CONTROLLED (`value`/
// `onValueChange`, driven by the URL), so this mock just renders the given
// `value`/`onValueChange` through a plain click handler instead of Radix's
// real activation logic.
vi.mock("@/components/ui/tabs", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const TabsCtx = React.createContext<{ value: string; setValue: (value: string) => void }>({
    value: "",
    setValue: () => {},
  });
  return {
    Tabs: ({
      value,
      onValueChange,
      children,
    }: {
      value: string;
      onValueChange: (value: string) => void;
      children?: ReactNode;
    }) => <TabsCtx.Provider value={{ value, setValue: onValueChange }}>{children}</TabsCtx.Provider>,
    TabsList: ({ children }: { children?: ReactNode }) => <div role="tablist">{children}</div>,
    TabsTrigger: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(TabsCtx);
      return (
        <button role="tab" type="button" aria-selected={ctx.value === value} onClick={() => ctx.setValue(value)}>
          {children}
        </button>
      );
    },
    TabsContent: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(TabsCtx);
      return ctx.value === value ? <div>{children}</div> : null;
    },
  };
});

const { getMock, featuresBox } = vi.hoisted(() => ({
  getMock: vi.fn(),
  featuresBox: { current: { printer_enabled: true } },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

const STATS: StatsOut = {
  models: { total: 12, favorites: 3, archived: 1, drafts: 2 },
  files: {
    total: 30,
    bytes_total: 1_500_000,
    bytes_by_backend: { local: 1_000_000, s3: 500_000 },
    by_format: { stl: 20, "3mf": 10 },
  },
  tags: 5,
  collections: 2,
  prints: { total: 8, succeeded: 6, failed: 2, filament_g_total: 1234.5, duration_s_total: 36000 },
  recent: { models_added_7d: 4, prints_7d: 3 },
  jobs: { running: 1, queued: 2, failed_24h: 1 },
  recent_models: [],
  recent_prints: [],
  material_usage: [],
};

const ARCHIVES: ArchiveOut[] = [
  {
    id: 1,
    print_job_id: 1,
    printer_id: 1,
    printer_name: "A1",
    origin: "app",
    subtask_name: "benchy.3mf",
    model_id: null,
    model_slug: null,
    model_name: "Benchy",
    revision_id: null,
    result: "completed",
    duration_s: 3600,
    est_filament: null,
    plate: null,
    fetch_state: "done",
    fetch_error: null,
    has_thumbnail: false,
    has_file: true,
    has_snapshot: false,
    has_timelapse: false,
    media_note: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-10-22T08:00:00Z",
    created_by_user_id: null,
    run: null,
  },
];

function mockGet(options: { stats?: StatsOut | null; features?: { printer_enabled: boolean } } = {}) {
  const stats = "stats" in options ? options.stats : STATS;
  featuresBox.current = options.features ?? { printer_enabled: true };
  getMock.mockImplementation((path: string) => {
    if (path === "/stats") return stats ? Promise.resolve(stats) : Promise.reject(new Error("boom"));
    if (path === "/features") return Promise.resolve(featuresBox.current);
    if (path.startsWith("/archives")) return Promise.resolve(ARCHIVES);
    if (path === "/printers") return Promise.resolve([{ id: 1, name: "A1" }]);
    return Promise.resolve({});
  });
}

function renderHistory(initialEntry = "/print/history") {
  const rootRoute = createRootRoute();
  const authenticatedRoute = createRoute({ id: "authenticated", getParentRoute: () => rootRoute });
  const historyRoute = createRoute({
    getParentRoute: () => authenticatedRoute,
    path: "/print/history",
    validateSearch: parsePrintHistorySearch,
    component: PrintHistoryPage,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([authenticatedRoute.addChildren([historyRoute])]),
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
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
  getMock.mockReset();
});

describe("PrintHistoryPage", () => {
  it("defaults to the runs tab and renders the archive table", async () => {
    mockGet();
    renderHistory();

    expect(await screen.findByText("Benchy")).toBeInTheDocument();
  });

  it("?tab=stats renders the stat tiles", async () => {
    mockGet();
    renderHistory("/print/history?tab=stats");
    expect(await screen.findByText("12")).toBeInTheDocument(); // models total
    expect(screen.getByText("Favorites")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument(); // files total
    expect(screen.getByText("Runs (14d)")).toBeInTheDocument();
  });

  it("switching tab updates the URL", async () => {
    mockGet();
    const router = renderHistory();
    await screen.findByText("Benchy");

    fireEvent.click(screen.getByRole("tab", { name: "Stats" }));

    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "stats" }));
  });

  it("hides the runs tab and tab list when the printer flag is off", async () => {
    mockGet({ features: { printer_enabled: false } });
    renderHistory();

    await screen.findByText("Runs (14d)");
    expect(screen.queryByRole("tab", { name: "Runs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Stats" })).not.toBeInTheDocument();
  });

  it("shows an error state when the stats fetch fails", async () => {
    mockGet({ stats: null });
    renderHistory("/print/history?tab=stats");

    expect(await screen.findByText("Couldn't load the dashboard")).toBeInTheDocument();
  });
});
