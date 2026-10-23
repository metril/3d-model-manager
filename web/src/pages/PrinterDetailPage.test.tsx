import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrinterOut, PrinterStatusOut, PrintersOverviewOut } from "@/api/types";
import { PrinterDetailPage } from "@/pages/PrinterDetailPage";

const { getMock, canPrinterBox, canBox } = vi.hoisted(() => ({
  canBox: { current: true },
  getMock: vi.fn(),
  canPrinterBox: { current: (_id: number, _kind: string): boolean => true },
}));

// HistoryStats has its own suite and needs a full /stats payload.
vi.mock("@/components/print/HistoryStats", () => ({ HistoryStats: () => <div data-testid="history-stats" /> }));

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: () => canBox.current, canPrinter: canPrinterBox.current }),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: getMock,
      post: () =>
        Promise.resolve({ stream_url: "/stream", snapshot_url: "/snapshot", expires_at: "2026-01-01T00:00:00Z" }),
    },
  };
});

const PRINTER: PrinterOut = {
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
};

const STATUS: PrinterStatusOut = {
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
};

const OVERVIEW: PrintersOverviewOut = {
  printers: [
    {
      printer: PRINTER,
      status: STATUS,
      active_job: null,
      last_runs: [],
      can_print: true,
      can_control: true,
      up_next: [],
    },
  ],
  up_next: [],
  recent_runs: [],
};

function mockGet(options: { printers?: PrinterOut[]; features?: { printer_enabled: boolean }; amsUnits?: number[] } = {}) {
  const printers = options.printers ?? [PRINTER];
  const features = options.features ?? { printer_enabled: true };
  getMock.mockImplementation((path: string) => {
    if (path === "/features") return Promise.resolve(features);
    if (path === "/printers") return Promise.resolve(printers);
    if (path === "/printers/overview") {
      const status = { ...STATUS, ams_units: (options.amsUnits ?? []).map((id) => ({ id })) };
      return Promise.resolve({ ...OVERVIEW, printers: [{ ...OVERVIEW.printers[0], status }] });
    }
    if (path.startsWith("/printers/1/ams-history")) return Promise.resolve({ unit: 0, step_s: 60, points: [{ ts: "2026-10-23T10:00:00Z", humidity: 30, temp: 25 }] });
    if (path === "/printers/1/status") return Promise.resolve(STATUS);
    if (path.startsWith("/archives")) return Promise.resolve([]);
    return Promise.resolve({});
  });
}

function renderDetail(id = "1") {
  const rootRoute = createRootRoute();
  const authenticatedRoute = createRoute({ id: "authenticated", getParentRoute: () => rootRoute });
  const detailRoute = createRoute({
    getParentRoute: () => authenticatedRoute,
    path: "/printers/$id",
    component: PrinterDetailPage,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([authenticatedRoute.addChildren([detailRoute])]),
    history: createMemoryHistory({ initialEntries: [`/printers/${id}`] }),
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
  canPrinterBox.current = () => true;
  canBox.current = true;
});

describe("PrinterDetailPage", () => {
  it("renders the printer's name, status, and a scoped history request", async () => {
    mockGet();
    renderDetail();

    expect((await screen.findAllByText("Bambu A1", {}, { timeout: 3000 })).length).toBeGreaterThan(0);
    expect(await screen.findByText("42%")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/archives?printer=1");
  });

  it("renders the XL card, the large camera slot, run history and stats", async () => {
    mockGet();
    renderDetail();

    await screen.findByRole("link", { name: "Bambu A1" });
    expect(document.querySelector('[data-slot="printer-card"]')).toHaveAttribute("data-size", "XL");
    expect(document.querySelector('[data-slot="camera-tile"]')).toBeInTheDocument();
    expect(screen.getByTestId("history-stats")).toBeInTheDocument();
    expect(screen.getByTestId("tile-bed")).toBeInTheDocument();
  });

  it("hides AMS history without units; with 2 units charts both", async () => {
    mockGet();
    const first = renderDetail();
    await screen.findByText("42%");
    expect(screen.queryByRole("button", { name: "AMS history" })).toBeNull();
    first.unmount();

    mockGet({ amsUnits: [0, 1] });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "AMS history" }));
    expect(await screen.findByRole("img", { name: /AMS unit 2 - Humidity/ })).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith(expect.stringMatching(/^\/printers\/1\/ams-history\?unit=1&from=/));
  });

  it("shows a not-found card for an unknown id", async () => {
    mockGet();
    renderDetail("99");

    expect(await screen.findByText("Printer not found")).toBeInTheDocument();
  });

  it("shows the disabled card when the printer flag is off", async () => {
    mockGet({ features: { printer_enabled: false } });
    renderDetail();

    expect(await screen.findByText("Printer integration is off")).toBeInTheDocument();
  });

  it("offers a Firmware button that opens the firmware modal", async () => {
    mockGet();
    renderDetail();
    expect(await screen.findByRole("button", { name: "Firmware" })).toBeInTheDocument();
  });

  it("hides Pause for a member without control", async () => {
    canPrinterBox.current = () => false;
    mockGet();
    renderDetail();

    await screen.findAllByText("Bambu A1", {}, { timeout: 3000 });
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
  });

  it("renders the CameraTile for a print-only viewer (no control)", async () => {
    canPrinterBox.current = (_id: number, kind: string) => kind === "print";
    mockGet();
    renderDetail();

    await screen.findAllByText("Bambu A1", {}, { timeout: 3000 });
    expect(document.querySelector('[data-slot="camera-tile"]')).toBeInTheDocument();
  });

  it("keeps the placeholder icon for a viewer without print access", async () => {
    canPrinterBox.current = () => false;
    mockGet();
    renderDetail();

    await screen.findAllByText("Bambu A1", {}, { timeout: 3000 });
    expect(document.querySelector('[data-slot="camera-tile"]')).not.toBeInTheDocument();
  });
});
