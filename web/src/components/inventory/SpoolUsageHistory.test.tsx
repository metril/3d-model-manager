import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SpoolDetailOut } from "@/api/types";
import { SpoolUsageHistory } from "@/components/inventory/SpoolUsageHistory";

const { authMock } = vi.hoisted(() => ({ authMock: vi.fn() }));
vi.mock("@/api/auth", () => ({ useAuth: () => authMock() }));

function detail(): SpoolDetailOut {
  return {
    id: 1,
    material_id: null,
    material_type: "PLA",
    brand: "Bambu",
    color_hex: "#ff0000",
    color_name: "Red",
    label_weight_g: 1000,
    empty_weight_g: 250,
    weight_used_g: 400,
    cost_per_kg: 20,
    location: "Shelf A",
    low_stock_g: 100,
    tray_uuid: null,
    archived_at: null,
    last_used_at: "2026-10-20T00:00:00Z",
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    remaining_g: 600,
    low_stock: false,
    slot: null,
    usage: [
      {
        id: 1,
        print_job_id: 5,
        archive_id: 9,
        model_name: "Benchy",
        grams: 400,
        source: "auto",
        created_at: "2026-10-01T00:00:00Z",
      },
    ],
    series: [
      { t: "2026-01-01T00:00:00Z", remaining_g: 1000 },
      { t: "2026-10-01T00:00:00Z", remaining_g: 600 },
    ],
  };
}

function renderHistory() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SpoolUsageHistory spool={detail()} />
    </QueryClientProvider>,
  );
}

describe("SpoolUsageHistory", () => {
  beforeEach(() => {
    authMock.mockReturnValue({ can: () => true, canPrinter: () => true });
  });

  it("renders the sparkline and usage rows", () => {
    renderHistory();
    expect(screen.getByRole("img", { name: "Remaining filament over time" })).toBeInTheDocument();
    expect(screen.getByText("Benchy")).toBeInTheDocument();
    expect(screen.getByText("400 g")).toBeInTheDocument();
    expect(screen.getByText("auto")).toBeInTheDocument();
  });
});
