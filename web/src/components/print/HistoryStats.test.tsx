import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FilamentStatsOut, StatsOut } from "@/api/types";
import { HistoryStats } from "@/components/print/HistoryStats";

const { filamentStatsMock, statsFiltersBox } = vi.hoisted(() => ({
  filamentStatsMock: vi.fn(),
  statsFiltersBox: { current: [] as unknown[] },
}));

const STATS: StatsOut = {
  models: { total: 1, favorites: 0, archived: 0, drafts: 0 },
  files: { total: 1, bytes_total: 0, bytes_by_backend: {}, by_format: {} },
  tags: 0,
  collections: 0,
  prints: { total: 0, succeeded: 0, failed: 0, filament_g_total: 0, duration_s_total: 0 },
  recent: { models_added_7d: 0, prints_7d: 0 },
  jobs: { running: 0, queued: 0, failed_24h: 0 },
  recent_models: [],
  recent_prints: [],
  material_usage: [],
};

const FILAMENT_STATS: FilamentStatsOut = {
  by_material: [
    {
      key: "1",
      name: "Galaxy Black PLA",
      color_hex: "#111111",
      total_g: 1200,
      total_cost: 24,
      points: [
        { t: "2026-09-01T00:00:00Z", grams: 400, cost: 8 },
        { t: "2026-09-08T00:00:00Z", grams: 800, cost: 16 },
      ],
    },
  ],
  by_spool: [
    {
      key: "7",
      name: "Spool #7",
      color_hex: "#ff0000",
      total_g: 300,
      total_cost: 6,
      points: [{ t: "2026-09-01T00:00:00Z", grams: 300, cost: 6 }],
    },
  ],
};

vi.mock("@/api/archives", () => ({ useArchives: () => ({ data: [] }) }));

vi.mock("@/api/stats", () => ({
  useStats: () => ({ data: STATS, isLoading: false, isError: false }),
  useFilamentStats: (filters: unknown) => {
    statsFiltersBox.current.push(filters);
    return filamentStatsMock();
  },
}));

function renderStats() {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: HistoryStats });
  const jobsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/system/jobs", component: () => null });
  const modelRoute = createRoute({ getParentRoute: () => rootRoute, path: "/models/$slug", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, jobsRoute, modelRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  statsFiltersBox.current = [];
  filamentStatsMock.mockReturnValue({ data: FILAMENT_STATS, isLoading: false, isError: false });
});

describe("HistoryStats -- filament usage (P5 Task 12)", () => {
  it("renders material rows with grams + cost totals", async () => {
    renderStats();

    expect(await screen.findByText("Galaxy Black PLA")).toBeInTheDocument();
    expect(screen.getByText("1200 g")).toBeInTheDocument();
    expect(screen.getByText("24.00")).toBeInTheDocument();
  });

  it("toggling to By spool shows spool rows instead", async () => {
    renderStats();
    await screen.findByText("Galaxy Black PLA");
    expect(screen.queryByText("Spool #7")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "By spool" }));

    expect(screen.getByText("Spool #7")).toBeInTheDocument();
    expect(screen.queryByText("Galaxy Black PLA")).not.toBeInTheDocument();
  });

  it("links a spool row to its inventory page", async () => {
    renderStats();
    await screen.findByText("Galaxy Black PLA");
    fireEvent.click(screen.getByRole("radio", { name: "By spool" }));

    expect(screen.getByRole("link", { name: /Spool #7/ })).toHaveAttribute("href", "/inventory/spools?spool=7");
  });

  it("defaults to a 90-day range", async () => {
    renderStats();
    await screen.findByText("Galaxy Black PLA");
    expect(statsFiltersBox.current.at(-1)).toEqual({ days: 90, bucket: "week" });
  });

  it("changing the range refetches with the new days", async () => {
    renderStats();
    await screen.findByText("Galaxy Black PLA");

    fireEvent.click(screen.getByRole("radio", { name: "30d" }));

    await waitFor(() => expect(statsFiltersBox.current.at(-1)).toEqual({ days: 30, bucket: "week" }));
  });
});
