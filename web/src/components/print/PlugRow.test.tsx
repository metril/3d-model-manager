import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PlugOut } from "@/api/types";
import { PlugRow } from "@/components/print/PlugRow";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

const plug = (o: Partial<PlugOut> = {}): PlugOut => ({
  id: 7,
  name: "Main",
  printer_id: 1,
  controls_printer_power: true,
  kind: "tasmota",
  config: {},
  auto_off: true,
  auto_off_after_min: 10,
  off_delay_mode: "time",
  off_temp_threshold: 40,
  auto_on_before_print: false,
  auto_off_after_drying: false,
  drying_off_delay_min: 0,
  power_alert_enabled: false,
  power_alert_high_w: null,
  power_alert_low_w: null,
  schedule_enabled: false,
  schedule_on_time: null,
  schedule_off_time: null,
  schedule_tz: "UTC",
  show_in_switchbar: false,
  energy_kwh: 0,
  last_state: "on",
  last_power_w: 85.4,
  last_polled_at: null,
  last_error: null,
  ...o,
});
function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
});

describe("PlugRow", () => {
  it("shows state/power/auto-off and toggles the printer-power plug off", async () => {
    getMock.mockResolvedValue([plug({ id: 3, controls_printer_power: false }), plug()]);
    postMock.mockResolvedValue(plug({ last_state: "off" }));
    wrap(<PlugRow printerId={1} />);
    expect(await screen.findByText("85 W")).toBeInTheDocument();
    expect(screen.getByText(/auto-off 10 min/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Smart plug power" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/plugs/7/off"));
  });

  it("renders nothing when the printer has no plug", async () => {
    getMock.mockResolvedValue([]);
    const { container } = wrap(<PlugRow printerId={1} />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
