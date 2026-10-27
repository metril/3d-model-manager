import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ControlsSection } from "./ControlsSection";
import { a1Mini, h2d, renderCard, x1c } from "./testUtils";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: vi.fn().mockResolvedValue([]) } };
});

describe("ControlsSection", () => {
  beforeEach(() => postMock.mockReset().mockResolvedValue({ ok: true }));

  it("H2D: active-nozzle toggle marks the active side and posts select; hidden on A1 Mini", async () => {
    const it = h2d({}, { active_extruder: 0 });
    renderCard(<ControlsSection printer={it.printer} status={it.status} canControl />);
    expect(await screen.findByTestId("extruder-right")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("extruder-left")).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByTestId("extruder-left"));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/4/control/extruder", { extruder_id: 1 }));
  });

  it("A1 Mini has no active-nozzle toggle", async () => {
    const it = a1Mini();
    renderCard(<ControlsSection printer={it.printer} status={it.status} canControl />);
    await screen.findByRole("button", { name: "Chamber light" });
    expect(screen.queryByTestId("extruder-left")).toBeNull();
  });

  it("light toggle posts the inverse of the current state", async () => {
    const it = a1Mini({}, { light_on: false });
    renderCard(<ControlsSection printer={it.printer} status={it.status} canControl />);
    fireEvent.click(await screen.findByRole("button", { name: "Chamber light" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/light", { on: true }));
  });

  it("double-toggling the light keeps the latest optimistic state until the single timer fires", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const it = a1Mini({}, { light_on: false });
      renderCard(<ControlsSection printer={it.printer} status={it.status} canControl />);
      const btn = await screen.findByRole("button", { name: "Chamber light" });
      fireEvent.click(btn); // -> on (timer T1 at +5 s)
      await waitFor(() => expect(btn).toHaveAttribute("aria-pressed", "true"));
      await act(async () => {
        vi.advanceTimersByTime(4000);
      });
      fireEvent.click(btn); // -> off
      await waitFor(() => expect(btn).toHaveAttribute("aria-pressed", "false"));
      fireEvent.click(btn); // -> on again; T1 must be cleared
      await waitFor(() => expect(btn).toHaveAttribute("aria-pressed", "true"));
      await act(async () => {
        vi.advanceTimersByTime(2000); // T1 would have fired and reset to the reported (off) state
      });
      expect(btn).toHaveAttribute("aria-pressed", "true");
      await act(async () => {
        vi.advanceTimersByTime(4000); // the latest timer resets to reported
      });
      expect(btn).toHaveAttribute("aria-pressed", "false");
    } finally {
      vi.useRealTimers();
    }
  });

  it("speed segmented control marks the active level and posts a change", async () => {
    const it = a1Mini({}, { speed_level: 2 });
    renderCard(<ControlsSection printer={it.printer} status={it.status} canControl />);
    expect(await screen.findByRole("radio", { name: "Standard" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "Sport" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/control/speed", { level: 3 }));
    expect(screen.getByRole("radio", { name: "Sport" })).toHaveAttribute("aria-checked", "true");
  });

  it("airduct control only with the capability (A1 absent, X1C absent too: capability false)", async () => {
    const it = a1Mini();
    renderCard(<ControlsSection printer={it.printer} status={it.status} canControl />);
    await screen.findByRole("button", { name: "Chamber light" });
    expect(screen.queryByRole("group", { name: "Airduct" })).toBeNull();
  });

  it("airduct present when capability on, and posts mode", async () => {
    const it = x1c();
    const printer = { ...it.printer, capabilities: { ...it.printer.capabilities!, airduct: true } };
    renderCard(<ControlsSection printer={printer} status={it.status} canControl />);
    fireEvent.click(await screen.findByRole("button", { name: "heating" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/2/control/airduct", { mode: "heating" }));
  });

  it("renders nothing without control permission", async () => {
    const it = a1Mini();
    const { container } = renderCard(<ControlsSection printer={it.printer} status={it.status} canControl={false} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector('[data-slot="controls-section"]')).toBeNull();
  });
});
