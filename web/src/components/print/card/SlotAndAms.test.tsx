import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FilamentsSection } from "./FilamentsSection";
import { StatusSection } from "./StatusSection";
import { a1Mini, renderCard, tray } from "./testUtils";

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

beforeEach(() => {
  [getMock, postMock, putMock, deleteMock].forEach((m) => m.mockReset());
  getMock.mockImplementation(async (url: string) => (url.includes("/objects") ? { job_id: null, plate: null, objects: [] } : []));
});

describe("plate-clear pill", () => {
  it("shows pill + button and posts clear-plate", async () => {
    postMock.mockResolvedValue({ awaiting_plate_clear: false });
    const it = a1Mini({}, { awaiting_plate_clear: true, gcode_state: "FINISH" });
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={null} lastRuns={[]} canControl />);
    expect(await screen.findByText("Plate needs clearing")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mark plate cleared" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/clear-plate"));
  });

  it("hides the button without control and the pill when not awaiting", async () => {
    const it = a1Mini({}, { awaiting_plate_clear: true });
    const { unmount } = renderCard(<StatusSection printerId={1} status={it.status} activeJob={null} lastRuns={[]} canControl={false} />);
    expect(await screen.findByText("Plate needs clearing")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark plate cleared" })).toBeNull();
    unmount();
    const idle = a1Mini({}, { gcode_state: "FINISH" });
    renderCard(<StatusSection printerId={1} status={idle.status} activeJob={null} lastRuns={[]} canControl />);
    await screen.findByText("Idle");
    expect(screen.queryByText("Plate needs clearing")).toBeNull();
  });
});

function section(canControl = true, label: string | null = null) {
  const it = a1Mini(
    {},
    {
      trays: [
        tray({ slot: 1, tray: 1, tray_type: "PLA", color: "#112233" }),
        tray({ ams_unit: 1, tray: 0, slot: 4, tray_type: "PETG", color: "#445566" }),
      ],
      ams_units: [{ id: 0, humidity: 3, temp: 24, serial: null, fw: null, label }],
    },
  );
  renderCard(<FilamentsSection printer={it.printer} status={it.status} canControl={canControl} />);
}

describe("slot configure + AMS rename", () => {
  it("configures a slot with device fields", async () => {
    postMock.mockResolvedValue({});
    section();
    fireEvent.pointerDown(await screen.findByRole("button", { name: /Slot 2 menu/ }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Configure slot…" }));
    const dlg = await screen.findByRole("dialog", { name: "Configure slot" });
    fireEvent.change(within(dlg).getByLabelText("Sub-brand"), { target: { value: "Basic" } });
    fireEvent.change(within(dlg).getByLabelText("Colour"), { target: { value: "ff8800" } });
    expect(within(dlg).getByTestId("slot-swatch")).toHaveStyle({ backgroundColor: "#ff8800" });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/slots/0/1", {
        tray_info_idx: "GFL99",
        tray_type: "PLA",
        sub_brand: "Basic",
        color: "FF8800",
        nozzle_min: 190,
        nozzle_max: 230,
        apply_device: true,
      }),
    );
  });

  it("uses unit and in-unit tray index in the URL for a second AMS", async () => {
    postMock.mockResolvedValue({});
    section();
    fireEvent.pointerDown(await screen.findByRole("button", { name: /Slot 5 menu/ }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Configure slot…" }));
    const dlg = await screen.findByRole("dialog", { name: "Configure slot" });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/slots/1/0", expect.objectContaining({ tray_type: "PETG" })));
  });

  it("empty AMS label clears via DELETE", async () => {
    deleteMock.mockResolvedValue({});
    section(true, "Dry box");
    fireEvent.pointerDown(await screen.findByRole("button", { name: "Dry box menu" }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename AMS…" }));
    const dlg = await screen.findByRole("dialog", { name: "Rename AMS" });
    fireEvent.change(within(dlg).getByLabelText("Label"), { target: { value: "  " } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/printers/1/ams/0/label"));
    expect(putMock).not.toHaveBeenCalled();
  });

  it("resets the slot on the device after confirm", async () => {
    deleteMock.mockResolvedValue(undefined);
    section();
    fireEvent.pointerDown(await screen.findByRole("button", { name: /Slot 2 menu/ }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Configure slot…" }));
    const dlg = await screen.findByRole("dialog", { name: "Configure slot" });
    fireEvent.click(within(dlg).getByRole("button", { name: "Reset slot" }));
    const confirm = await screen.findByRole("dialog", { name: "Reset this slot?" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Reset slot" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/printers/1/slots/0/1/device"));
  });

  it("renames the AMS and shows the label", async () => {
    putMock.mockResolvedValue({});
    section(true, "Dry box");
    expect(await screen.findByText("Dry box")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole("button", { name: "Dry box menu" }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename AMS…" }));
    const dlg = await screen.findByRole("dialog", { name: "Rename AMS" });
    fireEvent.change(within(dlg).getByLabelText("Label"), { target: { value: "Silk" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/printers/1/ams/0/label", { label: "Silk" }));
  });

  it("hides configure/rename without control", async () => {
    section(false);
    await screen.findByTestId("slot-1");
    expect(screen.queryByRole("button", { name: /menu/ })).toBeNull();
  });
});
