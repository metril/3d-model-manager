import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { kprofileTiming } from "@/api/kprofiles";
import type { KProfile } from "@/api/types";
import { FilamentsSection } from "../card/FilamentsSection";
import { a1Mini, tray } from "../card/testUtils";
import { KProfilesModal } from "./KProfilesModal";

const { getMock, postMock, putMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  deleteMock: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock, delete: deleteMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ canPrinter: () => true, can: () => true }) }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/components/inventory/SlotAssignPopover", () => ({
  SlotAssignPopover: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const P1: KProfile = { cali_idx: 3, setting_id: "PF111", filament_id: "GFL99", name: "Generic PLA", k_value: 0.02, n_coef: 1.4, extruder_id: 0, nozzle_diameter: "0.4", note: "tuned" };
const P2: KProfile = { ...P1, cali_idx: 4, setting_id: "PF222", name: "Silk PLA", k_value: 0.05, note: null };
const LIST = { profiles: [P1, P2], nozzle_diameter: "0.4" };

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function setup(over: { caps?: Record<string, unknown>; state?: string; trays?: ReturnType<typeof tray>[]; canControl?: boolean; withTray?: boolean } = {}) {
  const it = a1Mini({}, { gcode_state: over.state ?? "IDLE", trays: over.trays ?? [tray({ slot: 0, cali_idx: 3 }), tray({ slot: 1, tray: 1 })] });
  const printer = { ...it.printer, capabilities: { ...it.printer.capabilities!, kprofiles: true, ...over.caps } };
  wrap(
    <KProfilesModal
      printer={printer}
      status={it.status}
      canControl={over.canControl ?? true}
      tray={over.withTray ? it.status.trays[1] : undefined}
      open
      onOpenChange={() => {}}
    />,
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  [getMock, postMock, putMock, deleteMock].forEach((m) => m.mockReset());
  kprofileTiming.pollMs = 1;
  getMock.mockImplementation(async (url: string) => (url.includes("/kprofiles") ? LIST : []));
  postMock.mockResolvedValue({ ok: true });
});

describe("KProfilesModal", () => {
  it("lists profiles with used-by trays and current nozzle first", async () => {
    setup();
    expect(await screen.findByText("Generic PLA")).toBeInTheDocument();
    expect(within(screen.getByTestId("kprofile-3")).getByText("A1-1")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")[0]).toHaveTextContent("0.4 mm");
    expect(getMock).toHaveBeenCalledWith("/printers/1/kprofiles?nozzle=0.4");
  });

  it("polls through a 202 pending response", async () => {
    let n = 0;
    getMock.mockImplementation(async (url: string) =>
      url.includes("/kprofiles") ? (++n < 3 ? { status: "pending", request_id: "r1" } : LIST) : [],
    );
    setup();
    expect(await screen.findByText("Silk PLA")).toBeInTheDocument();
    const urls = getMock.mock.calls.map((c) => c[0] as string).filter((u) => u.includes("kprofiles"));
    expect(urls).toEqual([
      "/printers/1/kprofiles?nozzle=0.4",
      "/printers/1/kprofiles?nozzle=0.4&request_id=r1",
      "/printers/1/kprofiles?nozzle=0.4&request_id=r1",
    ]);
  });

  it("shows the empty state", async () => {
    getMock.mockResolvedValue({ profiles: [], nozzle_diameter: "0.4" });
    setup();
    expect(await screen.findByText(/No K-profiles/)).toBeInTheDocument();
  });

  it("shows extruder tabs only on dual-nozzle printers", async () => {
    setup({ caps: { dual_nozzle: true } });
    await screen.findByText("Generic PLA");
    expect(screen.getByRole("tab", { name: "Extruder 1" })).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/printers/1/kprofiles?nozzle=0.4&extruder=0");
  });

  it("edits a profile with 6-decimal numeric payload and validates K", async () => {
    setup();
    fireEvent.click((await within(await screen.findByTestId("kprofile-3")).findByText("Edit")));
    const k = screen.getByLabelText("K");
    fireEvent.change(k, { target: { value: "2.5" } });
    expect(screen.getByText("K must be between 0 and 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(k, { target: { value: "0.0312345678" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/kprofiles", {
        name: "Generic PLA",
        filament_id: "GFL99",
        k_value: 0.031235,
        n_coef: 1.4,
        nozzle_diameter: "0.4",
        extruder_id: 0,
        nozzle_id: undefined,
        setting_id: "PF111",
        cali_idx: 3,
      }),
    );
  });

  it("creates a new profile without setting_id", async () => {
    setup();
    await screen.findByText("Generic PLA");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Filament"), { target: { value: "GFG99" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    const body = postMock.mock.calls[0][1];
    expect(body).toMatchObject({ name: "New", filament_id: "GFG99", k_value: 0.02, n_coef: 0 });
    expect(body.setting_id).toBeUndefined();
  });

  it("deletes after confirm", async () => {
    deleteMock.mockResolvedValue({ ok: true });
    setup();
    fireEvent.click(within(await screen.findByTestId("kprofile-4")).getByText("Delete"));
    expect(deleteMock).not.toHaveBeenCalled();
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Delete K-profile?" })).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/printers/1/kprofiles/PF222?nozzle=0.4&cali_idx=4"));
  });

  it("saves an edited note and clears an emptied one", async () => {
    putMock.mockResolvedValue({});
    deleteMock.mockResolvedValue(undefined);
    setup();
    const note = await screen.findByLabelText("Note for Silk PLA");
    fireEvent.change(note, { target: { value: "hi" } });
    fireEvent.blur(note);
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/printers/1/kprofiles/PF222/note", { note: "hi" }));
    const n1 = screen.getByLabelText("Note for Generic PLA");
    fireEvent.change(n1, { target: { value: "" } });
    fireEvent.blur(n1);
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/printers/1/kprofiles/PF111/note"));
  });

  it("applies a profile to a picked tray", async () => {
    setup();
    fireEvent.click(within(await screen.findByTestId("kprofile-4")).getByText("Apply to tray…"));
    fireEvent.click(await screen.findByRole("combobox", { name: "Choose a tray" }));
    fireEvent.click(await screen.findByRole("option", { name: /A1-2/ }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/kprofiles/select", {
        ams_id: 0,
        tray_id: 1,
        cali_idx: 4,
        filament_id: "GFL99",
        nozzle_diameter: "0.4",
      }),
    );
  });

  it("links a profile to a spool by merging its existing links", async () => {
    getMock.mockImplementation(async (url: string) =>
      url === "/spools/7/kprofiles" ? [{ printer_id: 9, extruder: 0, nozzle_diameter: 0.4, k_value: 0.1, n_coef: 0, name: "x", cali_idx: 1, setting_id: "PFx" }] : url.includes("/printers/1/kprofiles") ? LIST : url.startsWith("/spools") ? [{ id: 7, material_type: "PLA", brand: "Bambu", color_name: "Red" }] : [],
    );
    putMock.mockResolvedValue([]);
    setup();
    fireEvent.click(within(await screen.findByTestId("kprofile-3")).getByText("Link to spool…"));
    fireEvent.click(await screen.findByRole("combobox", { name: "Choose a spool" }));
    fireEvent.click(await screen.findByRole("option", { name: /#7/ }));
    fireEvent.click(screen.getByRole("button", { name: "Link" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [url, body] = putMock.mock.calls[0];
    expect(url).toBe("/spools/7/kprofiles");
    expect(body).toHaveLength(2);
    expect(body[1]).toMatchObject({ printer_id: 1, extruder: 0, nozzle_diameter: 0.4, setting_id: "PF111", cali_idx: 3 });
  });

  it("re-linking the same printer/extruder/nozzle replaces rather than duplicates", async () => {
    getMock.mockImplementation(async (url: string) =>
      url === "/spools/7/kprofiles" ? [{ printer_id: 1, extruder: 0, nozzle_diameter: 0.4, k_value: 0.1, n_coef: 0, name: "old", cali_idx: 1, setting_id: "PFold" }] : url.includes("/printers/1/kprofiles") ? LIST : url.startsWith("/spools") ? [{ id: 7, material_type: "PLA", brand: null, color_name: null }] : [],
    );
    putMock.mockResolvedValue([]);
    setup();
    fireEvent.click(within(await screen.findByTestId("kprofile-3")).getByText("Link to spool…"));
    fireEvent.click(await screen.findByRole("combobox", { name: "Choose a spool" }));
    fireEvent.click(await screen.findByRole("option", { name: /#7/ }));
    fireEvent.click(screen.getByRole("button", { name: "Link" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const body = putMock.mock.calls[0][1];
    expect(body).toHaveLength(1);
    expect(body[0]).toMatchObject({ setting_id: "PF111", nozzle_diameter: 0.4 });
  });

  it("disables writes while printing and without control", async () => {
    setup({ state: "RUNNING" });
    await screen.findByText("Generic PLA");
    expect(screen.getByText("Editing is disabled while printing.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    expect(within(screen.getByTestId("kprofile-3")).getByText("Edit")).toBeDisabled();
    expect(within(screen.getByTestId("kprofile-3")).getByText("Delete")).toBeDisabled();
  });

  it("tray context highlights current and one-click sets for the tray", async () => {
    setup({ withTray: true, trays: [tray({ slot: 0, cali_idx: 3 }), tray({ slot: 1, tray: 1, cali_idx: 3 })] });
    expect(within(await screen.findByTestId("kprofile-3")).getByText("Current")).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("kprofile-4")).getByText("Set for this tray"));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/kprofiles/select", expect.objectContaining({ tray_id: 1, cali_idx: 4 })));
  });
});

describe("entry points", () => {
  function section(caps: Record<string, unknown>, canControl = true) {
    const it = a1Mini({}, { trays: [tray({ slot: 0, cali_idx: 3 })] });
    const printer = { ...it.printer, capabilities: { ...it.printer.capabilities!, ...caps } };
    wrap(<FilamentsSection printer={printer} status={it.status} canControl={canControl} />);
  }

  it("SlotCircle menu opens the modal and selects for the tray", async () => {
    section({ kprofiles: true });
    fireEvent.pointerDown(await screen.findByRole("button", { name: "Slot 1 menu" }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Pressure advance…" }));
    fireEvent.click(await within(await screen.findByTestId("kprofile-4")).findByText("Set for this tray"));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/kprofiles/select", expect.objectContaining({ ams_id: 0, tray_id: 0, cali_idx: 4 })));
  });

  it("hides the menu item without the capability", async () => {
    section({ kprofiles: false });
    fireEvent.pointerDown(await screen.findByRole("button", { name: "Slot 1 menu" }), { button: 0, ctrlKey: false });
    await screen.findByRole("menuitem", { name: "Configure slot…" });
    expect(screen.queryByRole("menuitem", { name: "Pressure advance…" })).toBeNull();
  });
});

describe("SlotConfigDialog K-profile picker", () => {
  it("selects the chosen profile after configuring the slot", async () => {
    const { SlotConfigDialog } = await import("../card/SlotConfigDialog");
    postMock.mockResolvedValue({ ok: true });
    wrap(<SlotConfigDialog printerId={1} tray={tray({ slot: 1, tray: 1 })} kprofiles nozzle="0.4" open onOpenChange={() => {}} />);
    fireEvent.click(await screen.findByRole("combobox", { name: "K-profile (pressure advance)" }));
    fireEvent.click(await screen.findByRole("option", { name: /Silk PLA/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/kprofiles/select", {
        ams_id: 0,
        tray_id: 1,
        cali_idx: 4,
        filament_id: "GFL99",
        nozzle_diameter: "0.4",
      }),
    );
    const order = postMock.mock.calls.map((c) => c[0] as string);
    expect(order.findIndex((u) => u.includes("/slots/"))).toBeLessThan(order.findIndex((u) => u.endsWith("/select")));
  });
});

describe("selectIds", () => {
  it("maps external spools to tray 0/1 and AMS trays to local ids", async () => {
    const { selectIds } = await import("./KProfilesModal");
    expect(selectIds(tray({ ams_unit: 255, tray: 254, slot: 254 }))).toEqual({ ams_id: 255, tray_id: 0 });
    expect(selectIds(tray({ ams_unit: 255, tray: 255, slot: 255 }))).toEqual({ ams_id: 255, tray_id: 1 });
    expect(selectIds(tray({ ams_unit: 1, tray: 2, slot: 6 }))).toEqual({ ams_id: 1, tray_id: 2 });
  });
});
