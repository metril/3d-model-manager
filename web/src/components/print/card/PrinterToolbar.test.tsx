import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardFooter } from "./CardFooter";
import { PrinterToolbar } from "./PrinterToolbar";
import type { CardSize } from "./cardSize";
import { a1Mini, renderCard } from "./testUtils";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: vi.fn().mockResolvedValue([]) } };
});

function toolbar(size: CardSize, status = {}, opts: { canPrint?: boolean; canControl?: boolean } = {}) {
  const it = a1Mini({}, status);
  const onOpen = vi.fn();
  renderCard(
    <PrinterToolbar
      printer={{ ...it.printer, capabilities: { ...it.printer.capabilities!, sd_browser: true } }}
      status={it.status}
      canControl={opts.canControl ?? true}
      canPrint={opts.canPrint ?? true}
      size={size}
      onOpen={onOpen}
    />,
  );
  return onOpen;
}

const openMore = async () =>
  fireEvent.pointerDown(await screen.findByRole("button", { name: "More" }), { button: 0, ctrlKey: false });

describe("PrinterToolbar", () => {
  beforeEach(() => postMock.mockReset().mockResolvedValue({ ok: true }));

  it("M: Move + Camera inline; Files, Options, Calibrate, Firmware, history in More", async () => {
    const onOpen = toolbar("M");
    expect(await screen.findByRole("button", { name: "Movement" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Camera" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Files" })).toBeNull();
    await openMore();
    for (const n of ["Files", "Print options", "Calibration", "Firmware", "Temperature history"])
      expect(await screen.findByRole("menuitem", { name: n })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Firmware" }));
    expect(onOpen).toHaveBeenCalledWith("firmware");
  });

  it("L: Files and Print options are promoted to icon buttons; XL adds text labels", async () => {
    toolbar("L");
    expect(await screen.findByRole("button", { name: "Files" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Print options" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Files" })).toHaveTextContent("");
  });

  it("XL buttons carry visible labels", async () => {
    toolbar("XL");
    expect(await screen.findByRole("button", { name: "Files" })).toHaveTextContent("Files");
  });

  it("without print/control permission only Firmware remains", async () => {
    toolbar("M", {}, { canPrint: false, canControl: false });
    await openMore();
    expect(await screen.findByRole("menuitem", { name: "Firmware" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Calibration" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Movement" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Camera" })).toBeNull();
  });

  it("movement popover: step + Z jog + home + extrude", async () => {
    toolbar("M", { nozzle_temper: 200 });
    fireEvent.click(await screen.findByRole("button", { name: "Movement" }));
    fireEvent.click(await screen.findByRole("radio", { name: "50 mm" }));
    fireEvent.click(screen.getByRole("button", { name: "Z+" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/jog", { axis: "Z", mm: 50 }));
    fireEvent.click(screen.getByRole("button", { name: "Home" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/home", {}));
    fireEvent.click(screen.getByRole("button", { name: "Retract" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/extrude", { mm: -10 }));
  });

  it("calibration confirms first; movement and calibration are disabled while printing", async () => {
    toolbar("M");
    await openMore();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Calibration" }));
    expect(postMock).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Calibrate" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/calibrate", {}));
  });

  it("disabled while printing", async () => {
    toolbar("M", { gcode_state: "RUNNING" });
    expect(await screen.findByRole("button", { name: "Movement" })).toBeDisabled();
    await openMore();
    expect(await screen.findByRole("menuitem", { name: "Calibration" })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("CardFooter primary action", () => {
  beforeEach(() => postMock.mockReset().mockResolvedValue({ ok: true }));
  const footer = (gcodeState: string, canControl = true) =>
    renderCard(<CardFooter printerId={1} canPrint canControl={canControl} online gcodeState={gcodeState} />);

  it("idle shows Print, running shows Pause + Stop instead", async () => {
    footer("RUNNING");
    expect(await screen.findByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Print" })).toBeNull();
  });

  it("Pause confirms first then posts", async () => {
    footer("RUNNING");
    fireEvent.click(await screen.findByRole("button", { name: "Pause" }));
    expect(postMock).not.toHaveBeenCalled();
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/pause"));
  });

  it("Stop requires confirmation then posts /stop", async () => {
    footer("RUNNING");
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(postMock).not.toHaveBeenCalled();
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/stop"));
  });

  it("Resume shown while paused; viewers without control keep Print", async () => {
    const { unmount } = footer("PAUSE");
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
    unmount();
    footer("RUNNING", false);
    expect(await screen.findByRole("button", { name: "Print" })).toBeInTheDocument();
  });
});
