import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { ApiError } from "@/api/client";
import { PrintDialog } from "@/components/print/PrintDialog";

const { getMock, postMock, toastSuccessMock, authDataBox, financeConfigBox, costCentersBox } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  authDataBox: {
    current: { id: 1, role: "member", permissions: [] as string[], printer_access: null as Record<string, { requires_review?: boolean }> | null },
  },
  financeConfigBox: { current: { billing_enabled: false } },
  costCentersBox: {
    current: [] as { id: number; name: string; owner_user_id: number | null; group_id: number | null; available: number | null }[],
  },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock },
  };
});

vi.mock("@/api/auth", () => ({
  useAuth: () => ({
    data: authDataBox.current,
    can: (p: string) => authDataBox.current.role === "admin" || authDataBox.current.permissions.includes(p),
  }),
}));

vi.mock("@/api/finance", () => ({
  useFinanceConfig: () => ({ data: financeConfigBox.current }),
  useCostCenters: () => ({ data: costCentersBox.current }),
}));

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

const RECIPE = {
  id: 9,
  name: "PLA fast",
  printer_profile_id: 1,
  process_profile_id: 2,
  filament_profile_ids: [],
  bed_type: "textured_plate",
  options: {},
  target_printer_id: null,
  target_printer_model: "A1",
  fanout: "single",
  n: null,
  created_by_user_id: null,
  created_at: "2026-06-01T00:00:00Z",
};

const PRINTER_PROFILE = {
  id: 1,
  name: "A1 printer profile",
  kind: "printer",
  printer_model: "A1",
  nozzle_mm: 0.4,
  source: "import",
  created_by_user_id: null,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

function mockApi({
  trays,
  slicerOk = true,
}: {
  trays?: unknown[];
  slicerOk?: boolean;
} = {}) {
  getMock.mockImplementation((path: string) => {
    if (path === "/printers/5/status") {
      return Promise.resolve({
        online: true,
        gcode_state: "IDLE",
        trays:
          trays ??
          [
            { slot: 1, ams_unit: 0, tray: 0, color: "#ff0000", material: "PLA", tray_type: "PLA", tray_uuid: null, remain_pct: 80, spool: null },
            { slot: 4, ams_unit: 1, tray: 0, color: "#ff0000", material: "PLA", tray_type: "PLA", tray_uuid: null, remain_pct: 60, spool: null },
          ],
      });
    }
    if (path.startsWith("/profiles")) {
      return Promise.resolve([PRINTER_PROFILE]);
    }
    if (path === "/printers") {
      return Promise.resolve([{ id: 5, name: "Printer A", model: "A1" }]);
    }
    if (path.startsWith("/models?")) {
      return Promise.resolve({ items: [{ id: 42, name: "Benchy" }], next_cursor: null });
    }
    if (path.startsWith("/files/")) {
      return Promise.resolve({ id: 10, rel_path: "a.gcode.3mf", format: "gcode_3mf" });
    }
    if (path === "/recipes") {
      return Promise.resolve([RECIPE]);
    }
    if (path === "/slicer/health") {
      return Promise.resolve({ configured: true, ok: slicerOk, version: slicerOk ? "1.2.5.7" : null });
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
  authDataBox.current = { id: 1, role: "member", permissions: [], printer_access: null };
  financeConfigBox.current = { billing_enabled: false };
  costCentersBox.current = [];
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

  it("shows no cost-center picker and 'Send now' when billing is off", async () => {
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("button", { name: "Send now" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Cost center")).not.toBeInTheDocument();
  });

  it("shows the cost-center picker and 'Send next' when billing is on, posting cost_center_id through enqueue", async () => {
    financeConfigBox.current = { billing_enabled: true };
    costCentersBox.current = [
      { id: 1, name: "Shared", owner_user_id: null, group_id: 2, available: 10 },
      { id: 2, name: "Mine", owner_user_id: 1, group_id: null, available: 5 },
    ];
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const picker = await screen.findByLabelText("Cost center");
    expect(picker).toBeInTheDocument();
    const sendNext = await screen.findByRole("button", { name: "Send next" });
    await waitFor(() => expect(sendNext).not.toBeDisabled());
    fireEvent.click(sendNext);

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/queue",
        expect.objectContaining({ file_id: 10, front: true, cost_center_id: 2 }),
      ),
    );
  });

  it("swaps 'Send now' for 'Send next' when the printer requires review, even with billing off", async () => {
    authDataBox.current = {
      id: 1,
      role: "member",
      permissions: [],
      printer_access: { "5": { requires_review: true } },
    };
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("button", { name: "Send next" })).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });

  it("I3: falls back to enqueue only on a queue_required 409, not other 409s", async () => {
    const original = postMock.getMockImplementation()!;
    postMock.mockImplementation((path: string, body?: unknown) => {
      if (path === "/print/send") {
        return Promise.reject(new ApiError(409, "printer is disabled"));
      }
      return original(path, body);
    });
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const sendNow = await screen.findByRole("button", { name: "Send now" });
    await waitFor(() => expect(sendNow).not.toBeDisabled());
    fireEvent.click(sendNow);

    expect(await screen.findByText("printer is disabled")).toBeInTheDocument();
    // never silently switched to the enqueue fallback for a non-queue_required 409
    expect(postMock).not.toHaveBeenCalledWith("/queue", expect.anything());
  });

  it("I3: falls back to enqueue on a queue_required 409", async () => {
    const original = postMock.getMockImplementation()!;
    postMock.mockImplementation((path: string, body?: unknown) => {
      if (path === "/print/send") {
        return Promise.reject(new ApiError(409, "queue_required"));
      }
      return original(path, body);
    });
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const sendNow = await screen.findByRole("button", { name: "Send now" });
    await waitFor(() => expect(sendNow).not.toBeDisabled());
    fireEvent.click(sendNow);

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/queue", expect.objectContaining({ file_id: 10, front: true })));
  });

  it("M4: Send next stays enabled even when the printer isn't ready", async () => {
    authDataBox.current = {
      id: 1,
      role: "member",
      permissions: [],
      printer_access: { "5": { requires_review: true } },
    };
    const original = postMock.getMockImplementation()!;
    postMock.mockImplementation((path: string, body?: unknown) => {
      if (path === "/print/plan") {
        return (original(path, body) as Promise<Record<string, unknown>>).then((plan) => ({
          ...plan,
          printers: [{ id: 5, name: "Printer A", model: "A1", idle: false, compatible: true, reason: "busy" }],
        }));
      }
      return original(path, body);
    });
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    fireEvent.change(await screen.findByRole("combobox", { name: "Tray for PLA" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const sendNext = await screen.findByRole("button", { name: "Send next" });
    await waitFor(() => expect(sendNext).not.toBeDisabled());
  });

  it("an archive source hits reprint for its initial plan", async () => {
    renderDialog({ kind: "archive", archiveId: 7 });

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/archives/7/reprint"));
  });

  it("disables Send when the reprint's pre-selected printer isn't idle/compatible", async () => {
    const notReadyPlan = basePlan({
      printers: [{ id: 5, name: "Printer A", model: "A1", idle: false, compatible: true, reason: "busy" }],
    });
    postMock.mockImplementation((path: string) => {
      if (path === "/archives/7/reprint" || path === "/print/plan") {
        return Promise.resolve(notReadyPlan);
      }
      return Promise.resolve({});
    });
    renderDialog({ kind: "archive", archiveId: 7 });

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/archives/7/reprint"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Printer A");

    await waitFor(() => expect(screen.getByRole("button", { name: "Send now" })).toBeDisabled());
  });

  it("labels AMS trays by unit/tray and the external slot as External", async () => {
    mockApi({
      trays: [
        { slot: 1, ams_unit: 0, tray: 1, color: "#ff0000", material: "PLA", tray_type: "PLA", tray_uuid: null, remain_pct: 80, spool: null },
        { slot: 254, ams_unit: 255, tray: 0, color: "#ff0000", material: "PLA", tray_type: "PLA", tray_uuid: null, remain_pct: null, spool: null },
      ],
    });
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));

    const select = await screen.findByRole("combobox", { name: "Tray for PLA" });
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toContain("AMS 0·1");
    expect(options).toContain("External");
  });

  it("a gcode_3mf file source offers no recipe radios", async () => {
    renderDialog({ kind: "file", fileId: 10, format: "gcode_3mf" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));
    expect(await screen.findByLabelText("As sliced")).toBeInTheDocument();
    expect(screen.queryByLabelText("PLA fast")).not.toBeInTheDocument();
  });

  it("an stl file source starts in recipe mode", async () => {
    renderDialog({ kind: "file", fileId: 10, format: "stl" });
    expect(await screen.findByLabelText("PLA fast")).toBeInTheDocument();
    expect(screen.queryByLabelText("As sliced")).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalledWith("/print/plan", expect.anything());
  });

  it("resolves a missing file format via GET /files/{id}", async () => {
    const base = getMock.getMockImplementation()!;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    getMock.mockImplementation((path: string) =>
      path === "/files/10" ? gate.then(() => ({ id: 10, rel_path: "a.stl", format: "stl" })) : base(path),
    );
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/files/10"));
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));
    await screen.findByRole("radiogroup");
    expect(screen.queryByLabelText("PLA fast")).not.toBeInTheDocument();
    release();
    expect(await screen.findByLabelText("PLA fast")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/files/10");
    expect(screen.queryByLabelText("As sliced")).not.toBeInTheDocument();
  });

  it("choosing a recipe swaps Send now for Slice and send / Add to queue", async () => {
    renderDialog({ kind: "file", fileId: 10, format: "stl" });
    fireEvent.click(await screen.findByLabelText("PLA fast"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("button", { name: "Slice and send" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to queue" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send now" })).not.toBeInTheDocument();
  });

  it("Slice and send posts the recipe with front:true and toasts the printer name", async () => {
    renderDialog({ kind: "file", fileId: 10, format: "stl" });
    fireEvent.click(await screen.findByLabelText("PLA fast"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Slice and send" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/queue",
        expect.objectContaining({ file_id: 10, recipe_id: 9, printer_id: 5, front: true }),
      ),
    );
    expect(toastSuccessMock).toHaveBeenCalledWith("Slicing, then sending to Printer A");
  });

  it("as-sliced Add to queue posts the file/plate/printer/mapping", async () => {
    renderDialog({ kind: "file", fileId: 10 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute("aria-current", "step"));

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add to queue" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/queue",
        expect.objectContaining({ file_id: 10, plate: 1, printer_id: 5, ams_mapping: [0, 2] }),
      ),
    );
  });

  it("disables only Slice and send (not Add to queue) when the slicer sidecar is down", async () => {
    mockApi({ slicerOk: false });
    renderDialog({ kind: "file", fileId: 10, format: "stl" });
    fireEvent.click(await screen.findByLabelText("PLA fast"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const sliceAndSend = await screen.findByRole("button", { name: "Slice and send" });
    expect(sliceAndSend).toBeDisabled();
    expect(sliceAndSend).toHaveAttribute("title", expect.stringContaining("Slicer sidecar unavailable"));
    // M6: "Add to queue" with a recipe only enqueues it for the dispatcher
    // to slice later -- it never calls the sidecar itself, so it must stay
    // enabled even while the sidecar is down.
    expect(screen.getByRole("button", { name: "Add to queue" })).toBeEnabled();
  });

  it("uses a width class that survives the base dialog's sm:max-w-sm", async () => {
    mockApi();
    renderDialog({ kind: "pick" });
    const dialog = await screen.findByRole("dialog");
    expect(dialog.className).toContain("sm:max-w-3xl");
    expect(dialog.className.split(" ")).not.toContain("max-w-3xl");
  });

  it("titles the dialog with the printer and lists recent models on the pick step", async () => {
    mockApi();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <PrintDialog open onOpenChange={() => {}} source={{ kind: "pick" }} printerId={5} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Print on Printer A")).toBeTruthy();
    expect(await screen.findByText("Benchy")).toBeTruthy();
    expect(screen.getByText("Recent models")).toBeTruthy();
  });

  it("disables Slice and send until a source file is picked (recipe mode, several sources)", async () => {
    mockApi();
    const base = getMock.getMockImplementation()!;
    getMock.mockImplementation((path: string) => {
      if (path === "/features") return Promise.resolve({ printer_enabled: true, slicer_configured: true });
      if (path === "/models/3/revisions") return Promise.resolve([{ id: 103 }]);
      if (path === "/revisions/103")
        return Promise.resolve({
          files: [
            { id: 21, rel_path: "a.stl", format: "stl" },
            { id: 22, rel_path: "b.stl", format: "stl" },
          ],
        });
      return base(path);
    });
    renderDialog({ kind: "model", modelId: 3 });
    expect(await screen.findByText("a.stl")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));
    fireEvent.click(await screen.findByLabelText("PLA fast"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const btn = await screen.findByRole("button", { name: "Slice and send" });
    expect(btn).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add to queue" })).toBeDisabled();
  });
});
