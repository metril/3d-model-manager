import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StatsOut } from "@/api/types";
import { DashboardPage } from "@/pages/DashboardPage";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

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
  recent_models: [
    {
      id: 1,
      slug: "gizmo",
      name: "Gizmo",
      description: null,
      tags: [],
      updated_at: "2026-09-01T12:00:00Z",
      created_at: "2026-08-01T12:00:00Z",
      file_count: 2,
      formats: ["stl"],
      cover: null,
      render_url: null,
      print_time_s: null,
      has_sliced: false,
      source_site: null,
      source_collection_id: null,
      source_collection_title: null,
      favorite: false,
      dims_mm: null,
      best_slicer_file: null,
      printable_file: null,
    },
  ],
  recent_prints: [
    {
      id: 1,
      model_id: 1,
      print_job_id: null,
      source: "manual",
      quality: null,
      failure_reason: null,
      filament_cost: null,
      machine_cost: null,
      energy_kwh: null,
      energy_cost: null,
      total_cost: null,
      created_by_user_id: null,
      model_slug: "gizmo",
      model_name: "Gizmo",
      printed_at: "2026-09-05T12:00:00Z",
      printer_name: "Bench Printer",
      filament: null,
      filament_g: 42,
      result: "success",
      duration_min: 90,
      notes: null,
      created_at: "2026-09-05T12:00:00Z",
      material_id: 1,
      material: { id: 1, name: "Silk Blue PLA", kind: "PLA", color: "#3366ff" },
    },
  ],
  material_usage: [{ material_id: 1, name: "Silk Blue PLA", grams: 420, prints: 3 }],
};

const EMPTY_STATS: StatsOut = {
  ...STATS,
  recent_models: [],
  recent_prints: [],
  material_usage: [],
};

function mockGet(stats: StatsOut | null = STATS) {
  getMock.mockImplementation((path: string) => {
    if (path === "/stats") {
      return stats ? Promise.resolve(stats) : Promise.reject(new Error("boom"));
    }
    return Promise.resolve({});
  });
}

// `DashboardPage` links to `/jobs` -- give it a router context (same memory
// router pattern as `SettingsPage.test.tsx`).
function renderDashboard() {
  const rootRoute = createRootRoute();
  const home = createRoute({ getParentRoute: () => rootRoute, path: "/", component: DashboardPage });
  const jobs = createRoute({ getParentRoute: () => rootRoute, path: "/jobs", component: () => null });
  const modelDetail = createRoute({
    getParentRoute: () => rootRoute,
    path: "/models/$slug",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([home, jobs, modelDetail]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
});

describe("DashboardPage", () => {
  it("shows a skeleton while loading", async () => {
    getMock.mockImplementation(() => new Promise(() => {})); // never resolves
    const { container } = renderDashboard();

    await waitFor(() =>
      expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0),
    );
  });

  it("renders stat tiles from the mocked stats response", async () => {
    mockGet();
    renderDashboard();

    expect(await screen.findByText("12")).toBeInTheDocument(); // models total
    expect(screen.getByText("Favorites")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument(); // files total
    expect(screen.getByText("Tags")).toBeInTheDocument();
  });

  it("renders the format histogram and storage-by-backend breakdown", async () => {
    mockGet();
    renderDashboard();

    expect(await screen.findByText("stl")).toBeInTheDocument();
    expect(screen.getByText("3mf")).toBeInTheDocument();
    expect(screen.getByText(/local —/)).toBeInTheDocument();
    expect(screen.getByText(/s3 —/)).toBeInTheDocument();
  });

  it("renders printer job counts linking to Jobs", async () => {
    mockGet();
    renderDashboard();

    const link = await screen.findByRole("link", { name: "View all jobs" });
    expect(link).toHaveAttribute("href", "/jobs");
  });

  it("shows an error state when the stats fetch fails", async () => {
    mockGet(null);
    renderDashboard();

    expect(await screen.findByText("Couldn't load the dashboard")).toBeInTheDocument();
  });

  it("renders recent models with a working link", async () => {
    mockGet();
    renderDashboard();

    const links = await screen.findAllByRole("link", { name: /Gizmo/ });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/models/gizmo");
    }
  });

  it("renders recent prints with a result badge and material chip", async () => {
    mockGet();
    renderDashboard();

    expect(await screen.findByText("Success")).toBeInTheDocument();
    expect(screen.getByText("Silk Blue PLA")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Gizmo" });
    expect(link).toHaveAttribute("href", "/models/gizmo");
  });

  it("renders material usage bars", async () => {
    mockGet();
    renderDashboard();

    expect(await screen.findByText("Silk Blue PLA — 420 g · 3 print(s)")).toBeInTheDocument();
  });

  it("renders two free-text material usage rows with distinct keys (no material_id)", async () => {
    mockGet({
      ...STATS,
      material_usage: [
        { material_id: null, name: "Generic Grey PLA", grams: 100, prints: 1 },
        { material_id: null, name: "Generic White PLA", grams: 200, prints: 2 },
      ],
    });
    renderDashboard();

    expect(await screen.findByText("Generic Grey PLA — 100 g · 1 print(s)")).toBeInTheDocument();
    expect(screen.getByText("Generic White PLA — 200 g · 2 print(s)")).toBeInTheDocument();
  });

  it("shows empty-state copy when recent models/prints/material usage are empty", async () => {
    mockGet(EMPTY_STATS);
    renderDashboard();

    expect(await screen.findByText("No models yet.")).toBeInTheDocument();
    expect(screen.getByText("No prints logged yet.")).toBeInTheDocument();
    expect(screen.getByText("No material usage logged yet.")).toBeInTheDocument();
  });
});
