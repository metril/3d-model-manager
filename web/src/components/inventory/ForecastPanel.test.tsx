import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Forecast } from "@/api/stockForecast";
import { ForecastPanel, StockForecastWidget } from "@/components/inventory/ForecastPanel";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true }) }));

const forecast: Forecast = {
  config: {
    enabled: true,
    lead_time_days: 7,
    safety_margin_value: 14,
    safety_margin_unit: "days",
    include_queue: true,
    notify: true,
  },
  skus: [
    {
      material: "PLA",
      brand: "Bambu",
      color: "Black",
      spool_count: 2,
      remaining_g: 400,
      demand_g: 100,
      daily_rate_g: 20,
      days_remaining: 15,
      days_until_reorder: -2,
      reorder_point_g: 450,
      lead_time_days: 7,
      status: "reorder",
      snoozed: false,
    },
    {
      material: "PETG",
      brand: null,
      color: "Red",
      spool_count: 1,
      remaining_g: 900,
      demand_g: 0,
      daily_rate_g: null,
      days_remaining: null,
      days_until_reorder: null,
      reorder_point_g: 0,
      lead_time_days: 7,
      status: "ok",
      snoozed: false,
    },
  ],
  queue_demand: [{ material: "pla", grams: 100 }],
  queue_shortfalls: [{ material: "pla", demand_g: 500, stock_g: 400 }],
  alert_count: 1,
  generated_at: "2026-10-25T00:00:00Z",
};

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ForecastPanel", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue(forecast);
    putMock.mockReset().mockResolvedValue(undefined);
  });

  it("lists SKUs with status and queue shortfalls", async () => {
    wrap(<ForecastPanel />);
    expect(await screen.findByText("Bambu PLA Black")).toBeTruthy();
    expect(screen.getByText("Reorder")).toBeTruthy();
    expect(screen.getByTestId("queue-shortfalls").textContent).toContain("needs 500 g");
    expect(getMock).toHaveBeenCalledWith("/spools/forecast");
  });

  it("snoozes an alerting SKU", async () => {
    wrap(<ForecastPanel />);
    fireEvent.click(await screen.findByText("Snooze"));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/spools/forecast/sku", {
        material: "PLA",
        brand: "Bambu",
        color: "Black",
        alerts_snoozed: true,
      }),
    );
  });

  it("widget shows SKUs closest to running out", async () => {
    wrap(<StockForecastWidget />);
    expect(await screen.findByText("Bambu PLA Black")).toBeTruthy();
    expect(screen.getByText("15 d")).toBeTruthy();
    expect(screen.queryByText("PETG Red")).toBeNull();
  });
});
