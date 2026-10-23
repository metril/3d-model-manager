import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hasBackup } from "./ams";
import { FilamentsSection } from "./FilamentsSection";
import { a1Mini, renderCard, tray, x1c, x1e } from "./testUtils";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: vi.fn().mockResolvedValue([]) } };
});
vi.mock("@/components/inventory/SlotAssignPopover", () => ({
  SlotAssignPopover: ({ children }: { children?: ReactNode }) => <div data-testid="slot-assign-popover">{children}</div>,
}));

const TRAYS = [
  tray({ slot: 0, tray: 0, color: "#FF0000", material: "PLA" }),
  tray({ slot: 1, tray: 1, color: "#00FF00", material: "PETG", remain_pct: 30 }),
];

describe("FilamentsSection", () => {
  beforeEach(() => postMock.mockReset().mockResolvedValue({ ok: true }));

  it("renders an AMS unit with humidity/temp and a circle per slot", async () => {
    const it = a1Mini({}, { trays: TRAYS, ams_units: [{ id: 0, humidity: 41, temp: 27, serial: "SER1", fw: "1.2" }] });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl />);
    expect(await screen.findByText("AMS 1")).toBeInTheDocument();
    expect(screen.getByText("41%")).toBeInTheDocument();
    expect(screen.getByText("27°")).toBeInTheDocument();
    expect(screen.getByTestId("slot-0")).toHaveStyle({ backgroundColor: "#FF0000" });
    expect(screen.getAllByRole("meter")).toHaveLength(2);
    expect(screen.getAllByTestId("slot-assign-popover")).toHaveLength(2);
  });

  it("hover reveals serial and firmware", async () => {
    const it = a1Mini({}, { trays: TRAYS, ams_units: [{ id: 0, humidity: 41, temp: 27, serial: "SER1", fw: "1.2" }] });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl />);
    fireEvent.focus((await screen.findByText("41%")).closest("span.font-mono")!);
    expect((await screen.findAllByText("Serial: SER1")).length).toBeGreaterThan(0);
  });

  it("loaded marker, mapping pill, low-stock flag", async () => {
    const spool = { id: 9, material_type: "PLA", brand: null, color_hex: null, color_name: null, low_stock: true };
    const it = a1Mini({}, { trays: [tray({ slot: 0, spool }), tray({ slot: 1, color: "#00FF00" })], tray_now: 1 });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl mapping={{ 0: "F1" }} />);
    expect(await screen.findByText("Low")).toBeInTheDocument();
    expect(screen.getByText("F1")).toBeInTheDocument();
    expect(screen.getByTestId("loaded-1")).toBeInTheDocument();
    expect(screen.queryByTestId("loaded-0")).toBeNull();
  });

  it("slot menu loads / unloads through the filament control route", async () => {
    const it = a1Mini({}, { trays: TRAYS });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl />);
    fireEvent.pointerDown(await screen.findByRole("button", { name: "Slot 2 menu" }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Load" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/filament", { action: "load", tray: 1 }));
    fireEvent.pointerDown(screen.getByRole("button", { name: "Slot 1 menu" }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Unload" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/filament", { action: "unload" }));
  });

  it("external spool card; AMS backup badge for duplicate filament", async () => {
    const it = a1Mini(
      {},
      {
        trays: [tray({ slot: 0 }), tray({ slot: 1, tray: 1 })],
        ext_tray: tray({ slot: 254, ams_unit: 255, tray: 254, color: "#0000FF", material: "ABS", tray_type: "ABS" }),
      },
    );
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl />);
    expect(await screen.findByText("External")).toBeInTheDocument();
    expect(screen.getByText("AMS backup")).toBeInTheDocument();
    expect(hasBackup([tray({ slot: 0 }), tray({ slot: 1, color: "#00FF00" })])).toBe(false);
  });

  it("drying button only with capability and handler (X1E)", async () => {
    const onDry = vi.fn();
    const it = x1e({}, { trays: TRAYS, ams_units: [{ id: 0, humidity: 60, temp: 25, serial: null, fw: null }] });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl onDry={onDry} />);
    fireEvent.click(await screen.findByRole("button", { name: "Dry" }));
    expect(onDry).toHaveBeenCalledWith(0);
  });

  it("X1C has no ams_drying capability: no Dry button", async () => {
    const it = x1c({}, { trays: TRAYS, ams_units: [{ id: 0, humidity: 60, temp: 25, serial: null, fw: null }] });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl onDry={() => {}} />);
    await screen.findByText("AMS 1");
    expect(screen.queryByRole("button", { name: "Dry" })).toBeNull();
  });

  it("A1 Mini never shows Dry; no section when no trays", async () => {
    const it = a1Mini({}, { trays: TRAYS, ams_units: [{ id: 0, humidity: 60, temp: 25, serial: null, fw: null }] });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl onDry={() => {}} />);
    await screen.findByText("AMS 1");
    expect(screen.queryByRole("button", { name: "Dry" })).toBeNull();
  });

  it("read-only viewers get plain circles", async () => {
    const it = a1Mini({}, { trays: TRAYS });
    renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl={false} />);
    await screen.findByTestId("slot-0");
    expect(screen.queryByTestId("slot-assign-popover")).toBeNull();
    expect(screen.queryByRole("button", { name: "Slot 1 menu" })).toBeNull();
  });
});
