import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TempFanTiles } from "./TempFanTiles";
import { REVERT_MS } from "./useOptimistic";
import { a1Mini, h2d, renderCard, x1c, x1e } from "./testUtils";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: vi.fn().mockResolvedValue([]) } };
});

describe("TempFanTiles", () => {
  beforeEach(() => postMock.mockReset().mockResolvedValue({ ok: true }));
  afterEach(() => vi.useRealTimers());

  it("A1 Mini: nozzle, bed, part fan only (chamber/aux/chamber-fan tiles absent)", async () => {
    const it = a1Mini();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    expect(await screen.findByTestId("tile-nozzle")).toBeInTheDocument();
    expect(screen.getByTestId("tile-bed")).toBeInTheDocument();
    expect(screen.getByTestId("tile-fan-part")).toBeInTheDocument();
    expect(screen.queryByTestId("tile-chamber")).toBeNull();
    expect(screen.queryByTestId("tile-fan-aux")).toBeNull();
    expect(screen.queryByTestId("tile-fan-chamber")).toBeNull();
    expect(screen.queryByTestId("tile-nozzle-r")).toBeNull();
  });

  it("A1 Mini has no untested tooltip trigger (verified)", async () => {
    const it = a1Mini();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    const tile = await screen.findByTestId("tile-bed");
    fireEvent.focus(tile);
    expect(screen.queryByText("Untested on this model")).toBeNull();
  });

  it("X1C: chamber + aux + chamber fan tiles present; untested tooltip on hover/focus", async () => {
    const it = x1c();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    expect(await screen.findByTestId("tile-chamber")).toBeInTheDocument();
    expect(screen.getByTestId("tile-fan-aux")).toBeInTheDocument();
    expect(screen.getByTestId("tile-fan-chamber")).toBeInTheDocument();
    fireEvent.focus(screen.getByRole("button", { name: "Set Bed" }));
    expect((await screen.findAllByText("Untested on this model")).length).toBeGreaterThan(0);
  });

  it("X1C: chamber tile is read-only (no heater) and its tooltip says so", async () => {
    const it = x1c({}, { chamber_temper: 31 });
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    const tile = await screen.findByTestId("tile-chamber");
    expect(tile).toHaveTextContent("31");
    expect(screen.queryByRole("button", { name: "Set Chamber" })).toBeNull();
    fireEvent.focus(tile.parentElement!);
    expect((await screen.findAllByText(/Chamber temperature is read-only on this model/)).length).toBeGreaterThan(0);
  });

  it("X1E: chamber heater is settable", async () => {
    const it = x1e();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    fireEvent.click(await screen.findByRole("button", { name: "Set Chamber" }));
    fireEvent.click(await screen.findByRole("button", { name: "Off" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/3/control/temperature", { target: "chamber", value: 0 }));
  });

  it("H2D: both nozzles show their own reading and each posts its extruder_id", async () => {
    const it = h2d();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    expect(await screen.findByTestId("tile-nozzle-r")).toHaveTextContent("215 → 220");
    expect(screen.getByTestId("tile-nozzle-l")).toHaveTextContent("31");
    fireEvent.click(screen.getByRole("button", { name: "Set Nozzle L 0.6" }));
    fireEvent.click(await screen.findByRole("button", { name: "PLA 220" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/4/control/temperature", { target: "nozzle", value: 220, extruder_id: 1 }),
    );
  });

  it("shows current -> target", async () => {
    const it = a1Mini({}, { bed_temper: 55, bed_target: 60 });
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    const bed = await screen.findByTestId("tile-bed");
    expect(bed).toHaveTextContent("55 → 60 °C");
  });

  it("bed popover: PLA preset sends 60 and shows it optimistically", async () => {
    const it = a1Mini({}, { bed_temper: 24, bed_target: 0 });
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    fireEvent.click(await screen.findByRole("button", { name: "Set Bed" }));
    fireEvent.click(await screen.findByRole("button", { name: "PLA 60" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/temperature", { target: "bed", value: 60 }));
    await waitFor(() => expect(screen.getByTestId("tile-bed")).toHaveTextContent("24 → 60"));
  });

  it("number input + Enter sets the nozzle; Off sends 0", async () => {
    const it = a1Mini();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    fireEvent.click(await screen.findByRole("button", { name: "Set Nozzle" }));
    const input = await screen.findByRole("spinbutton");
    fireEvent.change(input, { target: { value: "215" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/temperature", { target: "nozzle", value: 215 }));
  });

  it("reverts the optimistic target after 10 s if the status never matches", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const it = a1Mini({}, { bed_target: 0 });
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    fireEvent.click(await screen.findByRole("button", { name: "Set Bed" }));
    fireEvent.click(await screen.findByRole("button", { name: "PETG 80" }));
    await waitFor(() => expect(screen.getByTestId("tile-bed")).toHaveTextContent("→ 80"));
    await act(async () => {
      vi.advanceTimersByTime(REVERT_MS + 50);
    });
    expect(screen.getByTestId("tile-bed")).not.toHaveTextContent("→ 80");
  });

  it("fan popover 50% posts the fan command", async () => {
    const it = a1Mini();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl />);
    fireEvent.click(await screen.findByRole("button", { name: "Set Part fan" }));
    fireEvent.click(await screen.findByRole("button", { name: "50%" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/fan", { fan: "part", pct: 50 }));
  });

  it("read-only without control permission", async () => {
    const it = a1Mini();
    renderCard(<TempFanTiles printer={it.printer} status={it.status} canControl={false} />);
    await screen.findByTestId("tile-bed");
    expect(screen.queryByRole("button", { name: "Set Bed" })).toBeNull();
  });
});
