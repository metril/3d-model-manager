import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { PrintDialog } from "@/components/print/PrintDialog";

const { getMock, postMock, toastSuccessMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  toastSuccessMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock },
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccessMock, error: vi.fn() },
}));

// Radix's Select never reaches an interactive open state under jsdom -- swap
// it for a plain native <select>, same workaround as MaterialsSection.test.tsx
// / PrintsTab.test.tsx.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
    ...rest
  }: {
    value?: string;
    onValueChange: (value: string) => void;
    children?: ReactNode;
  } & Record<string, unknown>) => (
    <select value={value} onChange={(event) => onValueChange(event.target.value)} {...rest}>
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  SelectValue: () => null,
  SelectContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children?: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

function basePlan(overrides: Record<string, unknown> = {}) {
  return {
    file_id: 10,
    model_id: 42,
    plate: 1,
    plates: [{ index: 1, prediction_s: 1800, weight_g: 25, est_cost: 1.5, thumbnail_available: false, filaments: [] }],
    printer_id: 5,
    printers: [{ id: 5, name: "Printer A", model: "A1", idle: true, compatible: true, reason: null }],
    filaments: [
      { filament_id: 1, type: "PLA", color: "#ff0000", grams: 12, tray: null, status: "type", delta_e: null, spool: null },
      { filament_id: 2, type: "PETG", color: "#00ff00", grams: 8, tray: 2, status: "ok", delta_e: 0, spool: null },
    ],
    ams_mapping: [0, 2],
    use_ams: true,
    est_duration_s: 1800,
    est_grams: 20,
    est_cost: 1.5,
    warnings: [],
    options: { bed_levelling: true, flow_cali: true, timelapse: false },
    ...overrides,
  };
}

function mockApi() {
  getMock.mockImplementation((path: string) => {
    if (path === "/printers/5/status") {
      return Promise.resolve({
        online: true,
        gcode_state: "IDLE",
        trays: [
          { slot: 1, ams_unit: 0, tray: 0, color: "#ff0000", material: "PLA", tray_type: "PLA", tray_uuid: null, remain_pct: 80, spool: null },
          { slot: 4, ams_unit: 1, tray: 0, color: "#ff0000", material: "PLA", tray_type: "PLA", tray_uuid: null, remain_pct: 60, spool: null },
        ],
      });
    }
    return Promise.resolve({});
  });
  postMock.mockImplementation((path: string, body?: { overrides?: Record<number, number> }) => {
    if (path === "/print/plan") {
      const resolved = body?.overrides?.[1] === 4;
      return Promise.resolve(
        basePlan({
          filaments: [
            {
              filament_id: 1,
              type: "PLA",
              color: "#ff0000",
              grams: 12,
              tray: resolved ? 4 : null,
              status: resolved ? "ok" : "type",
              delta_e: resolved ? 0 : null,
              spool: null,
            },
            { filament_id: 2, type: "PETG", color: "#00ff00", grams: 8, tray: 2, status: "ok", delta_e: 0, spool: null },
          ],
        }),
      );
    }
    if (path === "/archives/7/reprint") {
      return Promise.resolve(basePlan());
    }
    if (path === "/print/send") {
      return Promise.resolve({ id: 1, printer_id: 5, file_id: 10, state: "queued" });
    }
    if (path === "/queue") {
      return Promise.resolve({ id: 1, model_id: 42, position: 1, added_at: "2026-06-01T00:00:00Z", model: {}, printable_file: null });
    }
    return Promise.resolve({});
  });
}

function renderDialog(source: Parameters<typeof PrintDialog>[0]["source"], onSent = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    onSent,
    ...render(
      <QueryClientProvider client={queryClient}>
        <PrintDialog open onOpenChange={() => {}} source={source} onSent={onSent} />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  toastSuccessMock.mockReset();
  mockApi();
});

describe("PrintDialog", () => {
  it("a file source with a single plate lands on Filament (Source + Plate + Profile skipped)", async () => {
    renderDialog({ kind: "file", fileId: 10 });

    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    expect(screen.getAllByText("PLA").length).toBeGreaterThan(0);
  });

  it("clicking the rail navigates back to an earlier step", async () => {
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));

    fireEvent.click(screen.getByRole("button", { name: "Profile" }));

    expect(screen.getByRole("button", { name: "Profile" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("As sliced")).toBeInTheDocument();
  });

  it("an override re-posts the plan with overrides and the row turns from red to plain", async () => {
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));

    const rowBefore = screen.getByText("Type mismatch").closest("[data-status]");
    expect(rowBefore).toHaveAttribute("data-status", "type");

    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/print/plan", expect.objectContaining({ overrides: { 1: 4 } })),
    );
    await waitFor(() => expect(screen.queryByText("Type mismatch")).not.toBeInTheDocument());
  });

  it("disables Send while an unresolved red row remains", async () => {
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));

    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("button", { name: "Send now" })).toBeDisabled();
  });

  it("Send now posts ams_mapping/toggles/printer and closes + toasts", async () => {
    const onOpenChange = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <PrintDialog open onOpenChange={onOpenChange} source={{ kind: "file", fileId: 10 }} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));

    // Resolve the red row first so Send isn't blocked.
    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });
    await waitFor(() => expect(screen.queryByText("Type mismatch")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const sendNow = await screen.findByRole("button", { name: "Send now" });
    await waitFor(() => expect(sendNow).not.toBeDisabled());
    fireEvent.click(sendNow);

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/print/send",
        expect.objectContaining({
          file_id: 10,
          printer_id: 5,
          ams_mapping: [0, 2],
          bed_levelling: true,
          flow_cali: true,
          timelapse: false,
        }),
      ),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toastSuccessMock).toHaveBeenCalledWith("Sent to printer");
  });

  it("Add to queue posts the model id", async () => {
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add to queue" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/queue", { model_id: 42 }));
  });

  it("an archive source hits reprint for its initial plan", async () => {
    renderDialog({ kind: "archive", archiveId: 7 });

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/archives/7/reprint"));
  });
});
