import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppSettings } from "@/api/types";
import { AutomationCard } from "@/components/settings/AutomationCard";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as SiteTokensCard.test.tsx), so the fakes have to be created
// through `vi.hoisted`.
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
    energy_cost_per_kwh: 0,
    ...overrides,
  };
}

function mockGet(settings: AppSettings, watchDir: string | null = null) {
  getMock.mockImplementation((path: string) => {
    if (path === "/settings/app") return Promise.resolve(settings);
    if (path === "/features")
      return Promise.resolve({
        printer_enabled: settings.printer_enabled,
        watch_dir: watchDir,
        watch_enabled: settings.watch_interval_s > 0,
      });
    return Promise.resolve({});
  });
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AutomationCard />
    </QueryClientProvider>,
  );
}

describe("AutomationCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  it("renders the four interval fields pre-filled from GET /settings/app", async () => {
    mockGet(fakeSettings());

    renderCard();

    expect(await screen.findByLabelText("Library scan interval (seconds)")).toHaveValue(3600);
    expect(screen.getByLabelText("Collection sync interval (seconds)")).toHaveValue(1800);
    expect(screen.getByLabelText("Watched folder poll (seconds)")).toHaveValue(30);
    expect(screen.getByLabelText("Watched folder stability delay (seconds)")).toHaveValue(5);
  });

  it("shows the watched folder path from useFeatures() when one is mounted", async () => {
    mockGet(fakeSettings(), "/data/watch");

    renderCard();

    expect(
      await screen.findByText("Watched folder: /data/watch — maps to WATCH_HOST_DIR on the host."),
    ).toBeInTheDocument();
  });

  it("shows 'No watched folder mounted.' when watch_dir is null", async () => {
    mockGet(fakeSettings());

    renderCard();

    expect(await screen.findByText("No watched folder mounted.")).toBeInTheDocument();
  });

  it("PUTs the edited body -- carrying printer_enabled through unchanged -- and shows Saved.", async () => {
    mockGet(fakeSettings({ printer_enabled: true }));
    putMock.mockResolvedValue(fakeSettings({ scan_interval_s: 60 }));

    renderCard();
    await screen.findByLabelText("Library scan interval (seconds)");

    fireEvent.change(screen.getByLabelText("Library scan interval (seconds)"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/app", {
        printer_enabled: true,
        scan_interval_s: 60,
        collection_sync_interval_s: 1800,
        watch_interval_s: 30,
        watch_stable_s: 5,
        filament_cost_per_kg: 20,
        machine_cost_per_hour: 0,
        energy_cost_per_kwh: 0,
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("M2: blocks save and shows an inline error when a field is cleared, instead of silently saving 0", async () => {
    mockGet(fakeSettings());

    renderCard();
    await screen.findByLabelText("Library scan interval (seconds)");

    fireEvent.change(screen.getByLabelText("Library scan interval (seconds)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Required -- enter 0 to turn this off.");
    expect(putMock).not.toHaveBeenCalled();
  });

  it("M2: blocks save with an inline error when a non-integer is typed into an int field", async () => {
    mockGet(fakeSettings());

    renderCard();
    await screen.findByLabelText("Collection sync interval (seconds)");

    fireEvent.change(screen.getByLabelText("Collection sync interval (seconds)"), {
      target: { value: "12.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Must be a whole number of seconds.");
    expect(putMock).not.toHaveBeenCalled();
  });

  it("M2: clearing the error re-enables save once the field is fixed", async () => {
    mockGet(fakeSettings());
    putMock.mockResolvedValue(fakeSettings());

    renderCard();
    await screen.findByLabelText("Library scan interval (seconds)");

    const input = screen.getByLabelText("Library scan interval (seconds)");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Required -- enter 0 to turn this off.");

    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
