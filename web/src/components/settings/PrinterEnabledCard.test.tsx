import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useFeatures } from "@/api/features";
import type { AppSettings } from "@/api/types";
import { PrinterEnabledCard } from "@/components/settings/PrinterEnabledCard";

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
    printer_enabled: false,
    scan_interval_s: 3600,
    collection_sync_interval_s: 3600,
    watch_interval_s: 30,
    watch_stable_s: 5,
    filament_cost_per_kg: 20,
    machine_cost_per_hour: 0,
    energy_cost_per_kwh: 0,
    ...overrides,
  };
}

// A minimal consumer of the `["features"]` query -- standing in for the real
// AppShell nav / PrinterPage / PrinterSetupCard / SendToPrinterButton /
// QueuePage / SlicerIntegrationCard. Proves the toggle's `PUT` invalidates
// that query so an ALREADY-MOUNTED consumer refetches and reflects the new
// value with no reload, even though `useFeatures` pins `staleTime: Infinity`.
function FeaturesConsumer() {
  const features = useFeatures();
  return <p>printer_enabled: {String(features.data?.printer_enabled)}</p>;
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PrinterEnabledCard />
      <FeaturesConsumer />
    </QueryClientProvider>,
  );
}

describe("PrinterEnabledCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  it("reflects the current printer_enabled value from GET /settings/app", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/settings/app") return Promise.resolve(fakeSettings({ printer_enabled: true }));
      if (path === "/features") return Promise.resolve({ printer_enabled: true, watch_dir: null, watch_enabled: false });
      return Promise.resolve({});
    });

    renderCard();

    expect(await screen.findByRole("switch", { name: "Enable printer integration" })).toBeChecked();
  });

  it("flips printer_enabled via PUT and invalidates the features query so an already-mounted consumer updates", async () => {
    let enabled = false;
    getMock.mockImplementation((path: string) => {
      if (path === "/settings/app") return Promise.resolve(fakeSettings({ printer_enabled: enabled }));
      if (path === "/features") return Promise.resolve({ printer_enabled: enabled, watch_dir: null, watch_enabled: false });
      return Promise.resolve({});
    });
    putMock.mockImplementation((_path: string, body: AppSettings) => {
      enabled = body.printer_enabled;
      return Promise.resolve(fakeSettings(body));
    });

    renderCard();
    expect(await screen.findByText("printer_enabled: false")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("switch", { name: "Enable printer integration" }));

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith(
        "/settings/app",
        expect.objectContaining({ printer_enabled: true, scan_interval_s: 3600 }),
      ),
    );
    expect(await screen.findByText("printer_enabled: true")).toBeInTheDocument();
  });

  it("disables the toggle while the update is pending", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/settings/app") return Promise.resolve(fakeSettings());
      if (path === "/features") return Promise.resolve({ printer_enabled: false, watch_dir: null, watch_enabled: false });
      return Promise.resolve({});
    });
    let resolvePut: (value: AppSettings) => void = () => {};
    putMock.mockImplementation(
      () =>
        new Promise<AppSettings>((resolve) => {
          resolvePut = resolve;
        }),
    );

    renderCard();
    const toggle = await screen.findByRole("switch", { name: "Enable printer integration" });
    fireEvent.click(toggle);

    await waitFor(() => expect(toggle).toBeDisabled());
    resolvePut(fakeSettings({ printer_enabled: true }));
  });
});
