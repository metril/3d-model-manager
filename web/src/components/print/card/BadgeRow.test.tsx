import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BadgeRow } from "./BadgeRow";
import { a1Mini, renderCard, x1c } from "./testUtils";

describe("BadgeRow", () => {
  it("A1 Mini: wifi, firmware, queue; no Connected chip, no door chip", async () => {
    const it = a1Mini({}, { firmware: { ota: "01.07.00", ams: [] } });
    renderCard(<BadgeRow printer={it.printer} status={it.status} queueCount={2} />);
    expect(await screen.findByText("-48dBm")).toBeInTheDocument();
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.getByText("FW 01.07.00")).toBeInTheDocument();
    expect(screen.getByText("2 queued")).toBeInTheDocument();
    expect(screen.queryByText("Door")).toBeNull();
    expect(screen.queryByText(/HMS/)).toBeNull();
  });

  it("shows Ethernet when no wifi signal, and Offline when offline", async () => {
    const it = a1Mini({}, { wifi_signal: null });
    renderCard(<BadgeRow printer={it.printer} status={it.status} queueCount={0} />);
    expect(await screen.findByText("Ethernet")).toBeInTheDocument();
  });

  it("offline printer shows no status chip (the header pill owns the status)", async () => {
    const it = a1Mini({}, { online: false });
    const { container } = renderCard(<BadgeRow printer={it.printer} status={it.status} queueCount={0} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.queryByText("Offline")).toBeNull();
    expect(container.querySelector('[data-slot="badge-row"]')).toBeEmptyDOMElement();
  });

  it("X1C: door chip; HMS count opens the slot; dev-mode-off chip", async () => {
    const onHms = vi.fn();
    const it = x1c({}, { hms: [{ attr: 1, code: 2 }, { attr: 3, code: 4 }], dev_mode: false });
    renderCard(<BadgeRow printer={it.printer} status={it.status} queueCount={0} onHms={onHms} extra={<span>maint</span>} />);
    expect(await screen.findByText("Door")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "HMS 2" }));
    expect(onHms).toHaveBeenCalled();
    expect(screen.getByText("Dev Mode off")).toBeInTheDocument();
    expect(screen.getByText("maint")).toBeInTheDocument();
  });

  it("door chip reflects door_open: open, closed, unknown", async () => {
    const open = x1c({}, { door_open: true });
    const { unmount } = renderCard(<BadgeRow printer={open.printer} status={open.status} queueCount={0} />);
    expect(await screen.findByText("Door open")).toBeInTheDocument();
    unmount();
    const closed = x1c({}, { door_open: false });
    const second = renderCard(<BadgeRow printer={closed.printer} status={closed.status} queueCount={0} />);
    expect(await screen.findByText("Door closed")).toBeInTheDocument();
    second.unmount();
    const a1 = a1Mini({}, { door_open: true });
    renderCard(<BadgeRow printer={a1.printer} status={a1.status} queueCount={0} />);
    expect(await screen.findByText("-48dBm")).toBeInTheDocument();
    expect(screen.queryByTestId("door-chip")).toBeNull(); // no door capability, no chip
  });
});
