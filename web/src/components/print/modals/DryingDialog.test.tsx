import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { AmsUnit } from "@/api/types";
import { DryingDialog } from "./DryingDialog";
import { a1Mini, tray } from "../card/testUtils";

const { getMock, postMock, toastError } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn(), toastError: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, delete: vi.fn() } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ canPrinter: () => true, can: () => true }) }));
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

const PRESETS = {
  PLA: { n3f_temp: 45, n3s_temp: 45, n3f_hours: 12, n3s_hours: 12 },
  PETG: { n3f_temp: 65, n3s_temp: 65, n3f_hours: 12, n3s_hours: 12 },
  TPU: { n3f_temp: 65, n3s_temp: 75, n3f_hours: 12, n3s_hours: 18 },
};

const unit = (o: Partial<AmsUnit> = {}): AmsUnit => ({ id: 0, humidity: 40, temp: 25, serial: null, fw: null, ...o });

function setup(opts: { unitId?: number; unit?: Partial<AmsUnit>; gcode?: string; caps?: Record<string, unknown> } = {}) {
  const base = a1Mini();
  const printer = { ...base.printer, capabilities: { ...base.printer.capabilities!, ams_drying: true, ...opts.caps } };
  const unitId = opts.unitId ?? 0;
  const status = {
    ...base.status,
    gcode_state: opts.gcode ?? "IDLE",
    trays: [tray({ slot: unitId === 0 ? 0 : 4, ams_unit: unitId, tray_type: "PLA" })],
    ams_units: [unit({ id: unitId, ...opts.unit })],
  };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <DryingDialog printer={printer} status={status} unitId={unitId} canControl open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  );
  return { onOpenChange };
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  getMock.mockReset().mockImplementation(async (path: string) => (path === "/drying/presets" ? { presets: PRESETS } : []));
  postMock.mockReset().mockResolvedValue({ ok: true });
  toastError.mockReset();
});

const inputs = () => screen.getAllByRole("spinbutton") as HTMLInputElement[];

describe("DryingDialog", () => {
  it("starts now with the backfilled preset of the loaded filament", async () => {
    const { onOpenChange } = setup();
    await waitFor(() => expect(inputs()[0].value).toBe("45"));
    fireEvent.click(screen.getByRole("button", { name: "Start drying" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/drying/start", {
        ams_id: 0,
        temp: 45,
        duration_hours: 12,
        filament: "PLA",
        rotate_tray: false,
      }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("preset buttons fill temp/hours per unit type (AMS HT uses n3s)", async () => {
    setup({ unitId: 128 });
    fireEvent.click(await screen.findByRole("button", { name: "TPU" }));
    expect(inputs()[0].value).toBe("75");
    expect(inputs()[1].value).toBe("18");
  });

  it("clamps temp and hours", async () => {
    setup();
    await waitFor(() => expect(inputs()[0].value).toBe("45"));
    fireEvent.change(inputs()[0], { target: { value: "200" } });
    fireEvent.blur(inputs()[0]);
    fireEvent.change(inputs()[1], { target: { value: "0" } });
    fireEvent.blur(inputs()[1]);
    expect(inputs()[0].value).toBe("85");
    expect(inputs()[1].value).toBe("1");
  });

  it("schedules at a time with start_after ISO", async () => {
    setup();
    await waitFor(() => expect(inputs()[0].value).toBe("45"));
    fireEvent.click(screen.getByRole("radio", { name: "At time" }));
    expect(screen.getByRole("button", { name: "Schedule drying" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Start at"), { target: { value: "2030-01-02T03:04" } });
    fireEvent.click(screen.getByRole("button", { name: "Schedule drying" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/printers/1/drying/schedules",
        expect.objectContaining({ ams_id: 0, filament: "PLA", start_after: new Date("2030-01-02T03:04").toISOString() }),
      ),
    );
  });

  it("offers After current print while printing and schedules without start_after", async () => {
    setup({ gcode: "RUNNING" });
    await waitFor(() => expect(inputs()[0].value).toBe("45"));
    expect(screen.getByRole("radio", { name: "Now" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Schedule drying" }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    expect(postMock.mock.calls[0][0]).toBe("/printers/1/drying/schedules");
    expect(postMock.mock.calls[0][1]).not.toHaveProperty("start_after");
  });

  it("hides After current print when drying while printing is allowed", async () => {
    setup({ gcode: "RUNNING", caps: { ams_drying_while_printing: true } });
    await waitFor(() => expect(inputs()[0].value).toBe("45"));
    expect(screen.queryByRole("radio", { name: "After current print" })).toBeNull();
    expect(screen.getByRole("button", { name: "Start drying" })).toBeEnabled();
  });

  it("lists blocked reasons and disables Start", async () => {
    setup({ unit: { dry_sf_reason: [1, 3] } });
    const panel = await screen.findByTestId("dry-blocked");
    expect(panel).toHaveTextContent("Insufficient power");
    expect(panel).toHaveTextContent("retract it first");
    expect(screen.getByRole("button", { name: "Start drying" })).toBeDisabled();
  });

  it("stops an active unit", async () => {
    setup({ unit: { dry_status: 2 } });
    fireEvent.click(await screen.findByRole("button", { name: "Stop drying" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/drying/stop", { ams_id: 0 }));
  });

  it("maps 409 screen_only to a friendly toast and stays open", async () => {
    postMock.mockRejectedValue(new ApiError(409, "raw", "screen_only"));
    const { onOpenChange } = setup();
    await waitFor(() => expect(inputs()[0].value).toBe("45"));
    fireEvent.click(screen.getByRole("button", { name: "Start drying" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(expect.stringContaining("its own screen")));
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
