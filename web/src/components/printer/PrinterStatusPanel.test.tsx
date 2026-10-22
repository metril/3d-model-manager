import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { PrinterStatusPanel } from "@/components/printer/PrinterStatusPanel";

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: () => true, canPrinter: () => true }),
}));


// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as ScanReport.test.tsx/SettingsPage.test.tsx), so the fakes
// have to be created through `vi.hoisted`.
const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock },
  };
});

function fakePrinter(overrides: Partial<PrinterOut> = {}): PrinterOut {
  return {
    id: 1,
    name: "Bambu A1",
    kind: "bambu_lan",
    host: "192.168.1.50",
    serial: "AC12345",
    model: "A1 mini",
    enabled: true,
    options: {},
    access_code_set: true,
    build_volume_mm: null,
    ...overrides,
  };
}

function fakeStatus(overrides: Partial<PrinterStatusOut> = {}): PrinterStatusOut {
  return {
    online: true,
    gcode_state: "RUNNING",
    mc_percent: 42,
    layer_num: 10,
    total_layer_num: 100,
    mc_remaining_time: 30,
    print_error: null,
    nozzle_temper: 210,
    bed_temper: 60,
    subtask_name: "benchy.gcode",
    wifi_signal: "-50dBm",
    trays: [],
    ...overrides,
  };
}

function renderPanel(printer: PrinterOut = fakePrinter()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PrinterStatusPanel printer={printer} />
    </QueryClientProvider>,
  );
}

describe("PrinterStatusPanel", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("renders percent, layer, and temps, and enables Pause while disabling Resume when RUNNING", async () => {
    getMock.mockResolvedValue(fakeStatus({ gcode_state: "RUNNING", mc_percent: 42 }));

    renderPanel();

    expect(await screen.findByText("42%")).toBeInTheDocument();
    expect(screen.getByText("layer 10/100")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Resume" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });

  it("renders the offline message and no controls when online is false", async () => {
    getMock.mockResolvedValue({ online: false } as PrinterStatusOut);

    renderPanel();

    expect(await screen.findByText(/Printer offline or printerd not running/)).toBeInTheDocument();
    expect(screen.getByText("offline")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });

  it("enables Resume and disables Pause when PAUSE", async () => {
    getMock.mockResolvedValue(fakeStatus({ gcode_state: "PAUSE" }));

    renderPanel();

    expect(await screen.findByRole("button", { name: "Resume" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });
});
