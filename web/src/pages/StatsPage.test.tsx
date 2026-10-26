import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StatsDashboard } from "@/api/stats";
import { StatsPage } from "@/pages/StatsPage";
import { loadPrefs } from "@/lib/statsLayout";

const payload: StatsDashboard = {
  days: 30,
  success_rate: { total: 10, completed: 8, failed: 1, canceled: 1, stopped: 0, success_rate: 0.8 },
  time_accuracy: { count: 2, mean_delta_pct: 4.2, mean_abs_delta_pct: 5, distribution: [{ label: "0-5%", count: 2 }] },
  failures: { total_failed: 1, by_error_code: [{ key: "0300", count: 1 }], by_hms: [], by_printer: [{ key: "1", name: "A1", failed: 1, total: 5, failure_rate: 0.2 }], by_material: [] },
  heatmap: { counts: Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => (d === 0 && h === 9 ? 3 : 0))), max: 3 },
  records: { longest_print: { archive_id: 1, name: "Benchy", value: 7200, printer_id: 1 }, most_filament: null, busiest_day: { date: "2026-10-01", prints: 4 } },
  printers: [{ printer_id: 1, name: "A1 Mini", hours: 12.5, jobs: 10, completed: 8, success_rate: 0.8, filament_g: 300 }],
  filament_trends: [
    { week: "2026-10-05T00:00:00Z", material: "PLA", grams: 100 },
    { week: "2026-10-12T00:00:00Z", material: "PLA", grams: 150 },
  ],
};

const get = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...a: unknown[]) => get(...a) }, ApiError: class extends Error {} }));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StatsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  get.mockReset();
  get.mockImplementation(async (url: string) =>
    url.startsWith("/auth/me") ? { id: 7 } : url.startsWith("/spools/forecast") ? { skus: [], alert_count: 0 } : payload,
  );
});

describe("StatsPage", () => {
  it("renders every widget from the dashboard payload", async () => {
    renderPage();
    expect(await screen.findByText("Benchy", { exact: false })).toBeInTheDocument();
    for (const name of ["Quick stats", "Success rate", "Time accuracy", "Failure analysis", "Print activity", "Records", "Per-printer", "Filament trends", "Stock forecast"]) {
      expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("A1 Mini")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/stats/dashboard?days=30");
  });

  it("changes the range, refetches and persists it per user", async () => {
    renderPage();
    await screen.findByText("Benchy", { exact: false });
    fireEvent.click(screen.getByRole("button", { name: "90 days" }));
    await waitFor(() => expect(get).toHaveBeenCalledWith("/stats/dashboard?days=90"));
    expect(loadPrefs(7).days).toBe(90);
  });

  it("resize button cycles the widget span and persists it", async () => {
    renderPage();
    await screen.findByText("Benchy", { exact: false });
    const card = document.querySelector('[data-widget="success"]') as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /Resize Success rate/ }));
    await waitFor(() => expect(loadPrefs(7).widgets.find((w) => w.id === "success")?.size).toBe(2));
  });

  it("restores a saved layout order", async () => {
    localStorage.setItem("tdmm.stats.layout.7", JSON.stringify({ widgets: [{ id: "records", size: 1 }], days: 30 }));
    renderPage();
    await screen.findByText("Benchy", { exact: false });
    await waitFor(() => {
      const first = document.querySelector("[data-widget]") as HTMLElement;
      expect(first.dataset.widget).toBe("records");
    });
  });
});
