import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppSettings } from "@/api/types";
import { PrintCostCard } from "@/components/settings/PrintCostCard";

const { getMock, putMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  putMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, put: putMock },
  };
});

function fakeSettings(overrides: Partial<AppSettings> = {}): AppSettings {
  return {
    printer_enabled: true,
    scan_interval_s: 3600,
    collection_sync_interval_s: 1800,
    watch_interval_s: 30,
    watch_stable_s: 5,
    filament_cost_per_kg: 20,
    machine_cost_per_hour: 0,
    energy_cost_per_kwh: 0.3,
    ...overrides,
  };
}

function mockGet(settings: AppSettings) {
  getMock.mockImplementation((path: string) => {
    if (path === "/settings/app") return Promise.resolve(settings);
    return Promise.resolve({});
  });
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PrintCostCard />
    </QueryClientProvider>,
  );
}

describe("PrintCostCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  it("renders the two rate fields pre-filled from GET /settings/app", async () => {
    mockGet(fakeSettings());

    renderCard();

    expect(await screen.findByLabelText("Filament cost (per kg)")).toHaveValue(20);
    expect(screen.getByLabelText("Machine cost (per hour)")).toHaveValue(0);
    expect(screen.getByLabelText("Energy cost (per kWh)")).toHaveValue(0.3);
  });

  it("PUTs the edited energy rate -- carrying the other fields through unchanged", async () => {
    mockGet(fakeSettings());
    putMock.mockResolvedValue(fakeSettings({ energy_cost_per_kwh: 0.5 }));

    renderCard();
    await screen.findByLabelText("Energy cost (per kWh)");

    fireEvent.change(screen.getByLabelText("Energy cost (per kWh)"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/app", {
        printer_enabled: true,
        scan_interval_s: 3600,
        collection_sync_interval_s: 1800,
        watch_interval_s: 30,
        watch_stable_s: 5,
        filament_cost_per_kg: 20,
        machine_cost_per_hour: 0,
        energy_cost_per_kwh: 0.5,
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("PUTs the edited body -- carrying the other fields through unchanged -- and shows Saved.", async () => {
    mockGet(fakeSettings());
    putMock.mockResolvedValue(fakeSettings({ filament_cost_per_kg: 30 }));

    renderCard();
    await screen.findByLabelText("Filament cost (per kg)");

    fireEvent.change(screen.getByLabelText("Filament cost (per kg)"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/app", {
        printer_enabled: true,
        scan_interval_s: 3600,
        collection_sync_interval_s: 1800,
        watch_interval_s: 30,
        watch_stable_s: 5,
        filament_cost_per_kg: 30,
        machine_cost_per_hour: 0,
        energy_cost_per_kwh: 0.3,
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("blocks save and shows an inline error when a field is cleared", async () => {
    mockGet(fakeSettings());

    renderCard();
    await screen.findByLabelText("Filament cost (per kg)");

    fireEvent.change(screen.getByLabelText("Filament cost (per kg)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Required");
    expect(putMock).not.toHaveBeenCalled();
  });

  it("blocks save on a negative value", async () => {
    mockGet(fakeSettings());

    renderCard();
    await screen.findByLabelText("Machine cost (per hour)");

    fireEvent.change(screen.getByLabelText("Machine cost (per hour)"), { target: { value: "-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("0 or greater");
    expect(putMock).not.toHaveBeenCalled();
  });
});
