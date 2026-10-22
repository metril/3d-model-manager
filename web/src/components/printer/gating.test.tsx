import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "@/api/auth";
import type { Features, FileOut, PrinterOut, PrinterStatusOut } from "@/api/types";
import { SendToPrinterButton } from "@/components/model-detail/SendToPrinterButton";
import { PrinterStatusPanel } from "@/components/printer/PrinterStatusPanel";

// Real useAuth/can/canPrinter; only the network is mocked, per-test /auth/me.
const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

const base: Me = {
  id: 1,
  username: "u",
  display_name: null,
  email: null,
  role: "member",
  permissions: [],
  groups: [],
  has_password: true,
  printer_access: {},
};
const viewer: Me = { ...base, role: "viewer" };
const member: Me = {
  ...base,
  permissions: ["printers:print", "printers:control"],
  printer_access: { "1": { print: true, control: false } },
};
const admin: Me = { ...base, role: "admin", permissions: ["admin"], printer_access: null };

const PRINTER: PrinterOut = {
  id: 1,
  name: "A1",
  kind: "bambu_lan",
  host: "h",
  serial: "s",
  model: "A1 mini",
  enabled: true,
  options: {},
  access_code_set: true,
  build_volume_mm: null,
};
const STATUS = { online: true, gcode_state: "RUNNING", trays: [] } as unknown as PrinterStatusOut;
const FILE = { id: 5, rel_path: "a.gcode.3mf", format: "gcode_3mf", meta: {} } as unknown as FileOut;

function setup(me: Me) {
  getMock.mockImplementation(async (url?: string) => {
    if (!url) return [];
    if (url === "/auth/me") return me;
    if (url.endsWith("/status")) return STATUS;
    if (url === "/features") return { printer_enabled: true } as Features;
    if (url === "/printers") return [PRINTER];
    return [];
  });
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("button gating", () => {
  beforeEach(() => getMock.mockReset());

  it("viewer: no Pause/Stop and no Print", async () => {
    setup(viewer);
    wrap(
      <>
        <PrinterStatusPanel printer={PRINTER} />
        <SendToPrinterButton file={FILE} />
      </>,
    );
    await screen.findByText(/layer/);
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /print a\.gcode/i })).not.toBeInTheDocument();
  });

  it("member without control: Print shown, Pause/Stop gated", async () => {
    setup(member);
    wrap(
      <>
        <PrinterStatusPanel printer={PRINTER} />
        <SendToPrinterButton file={FILE} />
      </>,
    );
    expect(await screen.findByRole("button", { name: /print a\.gcode/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });

  it("admin: sees Print, Pause and Stop", async () => {
    setup(admin);
    wrap(
      <>
        <PrinterStatusPanel printer={PRINTER} />
        <SendToPrinterButton file={FILE} />
      </>,
    );
    expect(await screen.findByRole("button", { name: /print a\.gcode/i })).toBeEnabled();
    expect(await screen.findByRole("button", { name: "Pause" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });
});
