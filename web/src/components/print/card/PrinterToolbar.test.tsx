import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardFooter } from "./CardFooter";
import { PrinterToolbar } from "./PrinterToolbar";
import type { CardSize } from "./cardSize";
import { a1Mini, renderCard } from "./testUtils";

const { postMock, getMock } = vi.hoisted(() => ({ postMock: vi.fn(), getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: getMock } };
});

function toolbar(size: CardSize, status = {}, opts: { canPrint?: boolean; canControl?: boolean; canAdmin?: boolean } = {}) {
  const it = a1Mini({}, status);
  const onOpen = vi.fn();
  renderCard(
    <PrinterToolbar
      printer={{ ...it.printer, capabilities: { ...it.printer.capabilities!, sd_browser: true } }}
      status={it.status}
      canControl={opts.canControl ?? true}
      canPrint={opts.canPrint ?? true}
      canAdmin={opts.canAdmin ?? false}
      size={size}
      onOpen={onOpen}
    />,
  );
  return onOpen;
}

const openMore = async () =>
  fireEvent.pointerDown(await screen.findByRole("button", { name: "More" }), { button: 0, ctrlKey: false });

describe("PrinterToolbar", () => {
  beforeEach(() => {
    postMock.mockReset().mockResolvedValue({ ok: true });
    getMock.mockReset().mockResolvedValue([]);
  });

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

  it("every size keeps Move + Camera inline; Files stays in the menu (XL adds text labels on inline tools)", async () => {
    toolbar("L");
    expect(await screen.findByRole("button", { name: "Movement" })).toHaveTextContent("");
    expect(screen.queryByRole("button", { name: "Files" })).toBeNull();
  });

  it("XL inline buttons carry visible labels", async () => {
    toolbar("XL");
    expect(await screen.findByRole("button", { name: "Movement" })).toHaveTextContent("Movement");
  });

  it("overflow menu is grouped Job / Maintain / Insight, with Info and (admin) Diagnose", async () => {
    const onOpen = toolbar("M", {}, { canAdmin: true });
    await openMore();
    for (const g of ["Job", "Maintain", "Insight"]) expect(await screen.findByText(g)).toBeInTheDocument();
    for (const n of ["Files", "Print options", "Calibration", "Firmware", "Temperature history", "Diagnose", "Info"])
      expect(screen.getByRole("menuitem", { name: n })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Diagnose" }));
    expect(onOpen).toHaveBeenCalledWith("diagnostic");
  });

  it("Diagnose is admin-only; Info is always there", async () => {
    const onOpen = toolbar("M");
    await openMore();
    expect(await screen.findByRole("menuitem", { name: "Info" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Diagnose" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Info" }));
    expect(onOpen).toHaveBeenCalledWith("info");
  });

  it("Skip objects (with count) lives in the Job group while printing and posts the selection", async () => {
    getMock.mockResolvedValue({
      job_id: 5,
      plate: 1,
      objects: [
        { id: 10, name: "Part A", plate: 1 },
        { id: 11, name: "Part B", plate: 1 },
      ],
    });
    toolbar("M", { gcode_state: "RUNNING" });
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    await openMore();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Skip objects (2)" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Part B" }));
    fireEvent.click(screen.getByRole("button", { name: /Skip 1 selected/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/skip-objects", { object_ids: [11] }));
  });

  it("no Skip entry with a single object", async () => {
    getMock.mockResolvedValue({ job_id: 5, plate: 1, objects: [{ id: 1, name: "Solo", plate: 1 }] });
    toolbar("M", { gcode_state: "RUNNING" });
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    await openMore();
    await screen.findByRole("menuitem", { name: "Firmware" });
    expect(screen.queryByRole("menuitem", { name: /Skip objects/ })).toBeNull();
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
  const footer = (gcodeState: string, canControl = true, online = true) =>
    renderCard(<CardFooter printerId={1} canPrint canControl={canControl} online={online} gcodeState={gcodeState} />);

  it("idle: only Print. printing: Pause (secondary) + Stop (outline). paused: Resume (filled) + Stop", async () => {
    const idle = footer("IDLE");
    expect(await screen.findByRole("button", { name: "Print" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    idle.unmount();
    const running = footer("RUNNING");
    expect(await screen.findByRole("button", { name: "Pause" })).toHaveAttribute("data-variant", "secondary");
    expect(screen.getByRole("button", { name: "Stop" })).toHaveAttribute("data-variant", "outline");
    expect(screen.queryByRole("button", { name: "Print" })).toBeNull();
    running.unmount();
    footer("PAUSE");
    expect(await screen.findByRole("button", { name: "Resume" })).toHaveAttribute("data-variant", "default");
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it("offline: no job actions, shows the unreachable note", async () => {
    footer("RUNNING", true, false);
    expect(await screen.findByText("Not reachable")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
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
