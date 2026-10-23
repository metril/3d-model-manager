import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PrinterCard } from "@/components/print/PrinterCard";
import { a1Mini, renderCard, tray, x1c } from "@/components/print/card/testUtils";

const { getMock, postMock, uploadMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn(), uploadMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});
vi.mock("@/api/upload", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/upload")>()),
  uploadFile: uploadMock,
}));
// `SlotAssignPopover` has its own suite; a marker tells us whether the card wrapped a slot in it.
vi.mock("@/components/inventory/SlotAssignPopover", () => ({
  SlotAssignPopover: ({ children }: { children?: ReactNode }) => <div data-testid="slot-assign-popover">{children}</div>,
}));

describe("PrinterCard", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue([]);
    postMock.mockReset().mockResolvedValue({ ok: true });
    uploadMock.mockReset();
    localStorage.clear();
  });

  it("links the header to the printer's detail page", async () => {
    renderCard(<PrinterCard item={a1Mini()} />);
    expect(await screen.findByRole("link", { name: "A1 Mini" })).toHaveAttribute("href", "/printers/1");
  });

  it("shows an Offline badge when the printer is offline and hides tiles/controls", async () => {
    renderCard(<PrinterCard item={a1Mini({}, { online: false })} />);
    expect((await screen.findAllByText("Offline")).length).toBeGreaterThan(0);
    expect(screen.queryByTestId("tile-bed")).toBeNull();
    expect(screen.queryByRole("button", { name: "Chamber light" })).toBeNull();
  });

  it("A1 Mini shows nozzle/bed tiles with current temps and no chamber tile", async () => {
    renderCard(<PrinterCard item={a1Mini({}, { nozzle_temper: 215, bed_temper: 60 })} />);
    expect(await screen.findByTestId("tile-nozzle")).toHaveTextContent("215");
    expect(screen.getByTestId("tile-bed")).toHaveTextContent("60");
    expect(screen.queryByTestId("tile-chamber")).toBeNull();
  });

  it("X1C shows chamber and the untested tooltip on controls", async () => {
    renderCard(<PrinterCard item={x1c()} />);
    expect(await screen.findByTestId("tile-chamber")).toBeInTheDocument();
    fireEvent.focus(screen.getByRole("button", { name: "Chamber light" }));
    expect((await screen.findAllByText("Untested on this model")).length).toBeGreaterThan(0);
  });

  it("badges a low-stock tray straight off tray.spool.low_stock", async () => {
    const spool = { id: 9, material_type: "PLA", brand: null, color_hex: "#FF0000", color_name: null, low_stock: true };
    renderCard(<PrinterCard item={a1Mini({}, { trays: [tray({ spool })] })} />);
    expect(await screen.findByText("Low")).toBeInTheDocument();
  });

  it("renders a slot circle per AMS tray, wrapped in SlotAssignPopover for controllers only", async () => {
    const trays = [tray({ slot: 0 }), tray({ slot: 1, tray: 1, color: "#00FF00", material: "PETG", tray_type: "PETG" })];
    const { unmount } = renderCard(<PrinterCard item={a1Mini({ can_control: true }, { trays })} />);
    expect(await screen.findAllByTestId("slot-assign-popover")).toHaveLength(2);
    unmount();
    renderCard(<PrinterCard item={a1Mini({ can_control: false }, { trays })} />);
    await screen.findByTestId("slot-0");
    expect(screen.queryByTestId("slot-assign-popover")).toBeNull();
  });

  it("shows Idle when there's no active job", async () => {
    renderCard(<PrinterCard item={a1Mini()} />);
    await screen.findByRole("link", { name: "A1 Mini" });
    expect(screen.getAllByText("Idle").length).toBeGreaterThan(0);
  });

  it("renders the badge and camera slots", async () => {
    renderCard(<PrinterCard item={a1Mini()} badge={<span>Primary</span>} camera={<span>Live feed</span>} />);
    expect(await screen.findByText("Primary")).toBeInTheDocument();
    expect(screen.getByText("Live feed")).toBeInTheDocument();
  });

  it("header/badge/footer extra slots accept nodes or render props with the card context", async () => {
    renderCard(
      <PrinterCard
        item={a1Mini()}
        headerExtra={<span>hx</span>}
        badgeExtra={(ctx) => <span>bx {ctx.printer.name}</span>}
        footerExtra={(ctx) => <span>fx {String(ctx.canControl)}</span>}
      />,
    );
    expect(await screen.findByText("hx")).toBeInTheDocument();
    expect(screen.getByText("bx A1 Mini")).toBeInTheDocument();
    expect(screen.getByText("fx true")).toBeInTheDocument();
  });

  it("scales the body per size via --pc-s", async () => {
    const { unmount } = renderCard(<PrinterCard item={a1Mini()} size="L" />);
    await screen.findByRole("link", { name: "A1 Mini" });
    expect(document.querySelector<HTMLElement>('[data-slot="printer-card"]')!.style.getPropertyValue("--pc-s")).toBe("1.2");
    unmount();
    renderCard(<PrinterCard item={a1Mini()} size="XL" />);
    await screen.findByRole("link", { name: "A1 Mini" });
    expect(document.querySelector<HTMLElement>('[data-slot="printer-card"]')!.style.getPropertyValue("--pc-s")).toBe("1.4");
  });

  it("S is compact; click expands to M with a Back pill that collapses again", async () => {
    renderCard(<PrinterCard item={a1Mini({}, { gcode_state: "RUNNING", mc_percent: 30 })} size="S" />);
    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");
    expect(screen.queryByTestId("tile-bed")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Expand A1 Mini" }));
    expect(await screen.findByTestId("tile-bed")).toBeInTheDocument();
    expect(document.querySelector('[data-slot="printer-card"]')).toHaveAttribute("data-size", "M");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(screen.queryByTestId("tile-bed")).toBeNull());
  });

  it("bulk-select checkbox calls back", async () => {
    const onSel = vi.fn();
    renderCard(<PrinterCard item={a1Mini()} selected={false} onSelectedChange={onSel} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select A1 Mini" }));
    expect(onSel).toHaveBeenCalledWith(true);
  });

  it("dropping a .3mf on the card creates a model, uploads and queues it for this printer", async () => {
    postMock.mockImplementation((url: string) =>
      url === "/models" ? Promise.resolve({ id: 7, current_revision: { id: 70 } }) : Promise.resolve({ id: 1 }),
    );
    uploadMock.mockResolvedValue({ job_id: "j" });
    renderCard(<PrinterCard item={a1Mini()} />);
    await screen.findByRole("link", { name: "A1 Mini" });
    const file = new File(["x"], "cube.3mf");
    fireEvent.drop(document.querySelector('[data-slot="printer-card"]')!, { dataTransfer: { items: [], files: [file] } });
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/queue", expect.objectContaining({ model_id: 7, printer_id: 1 })));
    expect(uploadMock).toHaveBeenCalledWith(expect.objectContaining({ modelId: 7, revisionId: 70, relPath: "cube.3mf" }));
  });

  it("does not accept drops without print permission", async () => {
    renderCard(<PrinterCard item={a1Mini({ can_print: false })} />);
    await screen.findByRole("link", { name: "A1 Mini" });
    fireEvent.drop(document.querySelector('[data-slot="printer-card"]')!, {
      dataTransfer: { items: [], files: [new File(["x"], "cube.3mf")] },
    });
    await new Promise((r) => setTimeout(r, 30));
    expect(uploadMock).not.toHaveBeenCalled();
  });
});
