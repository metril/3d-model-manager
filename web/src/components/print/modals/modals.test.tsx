import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { PrinterOut } from "@/api/types";
import { DiagnosticModal, FirmwareModal, HmsModal, PrinterInfoModal } from "@/components/print/modals";

const { getMock, postMock, perms } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  perms: { settings: true, control: true },
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({ can: () => perms.settings, canPrinter: () => perms.control }),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const FAULT = {
  code: "0300-0100",
  full_code: "03000100",
  kind: "hms" as const,
  severity: "serious",
  severity_level: 2,
  module: "Motion",
  description: "Y axis homing failed",
  wiki_url: "https://wiki.bambulab.com/x",
  actions: ["retry"],
};

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  perms.settings = true;
  perms.control = true;
});

describe("HmsModal", () => {
  it("lists faults, runs a remedy, clears, and renders unknown codes", async () => {
    getMock.mockResolvedValue([FAULT, { ...FAULT, code: "FFFF-0001", full_code: "FFFF0001", description: null, actions: [] }]);
    postMock.mockResolvedValue(undefined);
    wrap(<HmsModal printerId={3} open onOpenChange={() => {}} />);
    expect(await screen.findByText("Y axis homing failed")).toBeInTheDocument();
    expect(screen.getByText("Unknown code")).toBeInTheDocument();
    expect(screen.getAllByText(/Wiki/)[0].closest("a")).toHaveAttribute("href", FAULT.wiki_url);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/3/hms/action", { print_error: "03000100", action: "retry" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear errors" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/3/hms/clear"));
  });
});

const FW = {
  model_supported: true,
  current: "01.05.00",
  latest: "01.06.00",
  release_notes: "Fixes",
  release_time: null,
  checked_at: null,
  download_available: true,
  update_available: true,
  upload: null,
};

describe("permission gating", () => {
  it("hides Clear and remedy buttons without control", async () => {
    perms.control = false;
    getMock.mockResolvedValue([FAULT]);
    wrap(<HmsModal printerId={3} open onOpenChange={() => {}} />);
    expect(await screen.findByText("Y axis homing failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear errors" })).not.toBeInTheDocument();
  });

  it("hides Stage to SD card without settings:write or control", async () => {
    getMock.mockResolvedValue(FW);
    for (const k of ["settings", "control"] as const) {
      perms.settings = true;
      perms.control = true;
      perms[k] = false;
      const { unmount } = wrap(<FirmwareModal printerId={3} open onOpenChange={() => {}} />);
      expect(await screen.findByText("01.06.00")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Stage to SD card" })).not.toBeInTheDocument();
      unmount();
    }
  });
});

describe("FirmwareModal", () => {
  it("shows versions and stages to SD", async () => {
    getMock.mockResolvedValue(FW);
    postMock.mockResolvedValue({});
    wrap(<FirmwareModal printerId={3} open onOpenChange={() => {}} />);
    expect(await screen.findByText("01.06.00")).toBeInTheDocument();
    expect(screen.getByText(/printer's own screen/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stage to SD card" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/3/firmware/upload"));
    expect(await screen.findByText(/Confirm the update on the printer screen/)).toBeInTheDocument();
  });

  it("explains a 409", async () => {
    getMock.mockResolvedValue(FW);
    postMock.mockRejectedValue(new ApiError(409, "no firmware update available to stage"));
    wrap(<FirmwareModal printerId={3} open onOpenChange={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Stage to SD card" }));
    expect(await screen.findByText(/No firmware download is available/)).toBeInTheDocument();
  });
});

describe("DiagnosticModal", () => {
  it("runs for a saved printer and shows hints", async () => {
    getMock.mockResolvedValue({
      ok: false,
      checks: [
        { id: "tcp_8883", status: "ok", hint: "MQTT port reachable", detail: null },
        { id: "mqtt_auth", status: "fail", hint: "Check the access code", detail: null },
      ],
    });
    wrap(<DiagnosticModal printerId={3} open onOpenChange={() => {}} />);
    expect(await screen.findByText("Check the access code")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/printers/3/diagnostic");
  });

  it("runs ad hoc before save", async () => {
    postMock.mockResolvedValue({ ok: true, checks: [{ id: "tcp_8883", status: "ok", hint: "reachable", detail: null }] });
    const adhoc = { host: "10.0.0.2", serial: "S1", access_code: "abc" };
    wrap(<DiagnosticModal adhoc={adhoc} open onOpenChange={() => {}} />);
    expect(await screen.findByText("reachable")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/printers/diagnostic", adhoc);
  });
});

describe("PrinterInfoModal", () => {
  const printer: PrinterOut = {
    id: 3,
    name: "Mini",
    kind: "bambu_lan",
    host: "10.0.0.5",
    serial: "SER1",
    model: "A1 mini",
    enabled: true,
    options: {},
    access_code_set: true,
    build_volume_mm: null,
    total_print_hours: 12.34,
    capabilities: {
      model_key: "x1c",
      chamber_heater: true,
      chamber_temp: true,
      airduct: false,
      door: false,
      dual_nozzle: false,
      aux_fan: false,
      chamber_fan: false,
      camera: "rtsp",
      lidar: false,
      max_bed: 110,
      max_nozzle: 300,
      ams_drying: false,
      verified: false,
    },
  };
  it("shows details, capabilities and the untested marker", () => {
    wrap(
      <PrinterInfoModal
        printer={printer}
        status={{ online: true, gcode_state: "IDLE", firmware: { ota: "01.05.00" }, dev_mode: true, nozzle_type: "hardened" } as never}
        open
        onOpenChange={() => {}}
      />,
    );
    expect(screen.getByText("10.0.0.5")).toBeInTheDocument();
    expect(screen.getByText("01.05.00")).toBeInTheDocument();
    expect(screen.getByText("12.3 h")).toBeInTheDocument();
    expect(screen.getByText("Chamber heater")).toBeInTheDocument();
    expect(screen.getByText("untested on this model")).toBeInTheDocument();
  });
});
