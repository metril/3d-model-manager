import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlugSwitchbar } from "@/components/shell/PlugSwitchbar";
import { PlugsPage } from "@/pages/PlugsPage";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ can: () => true }),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

const base = {
  printer_id: null,
  controls_printer_power: true,
  kind: "rest",
  config: {},
  auto_off: false,
  auto_off_after_min: null,
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
  last_polled_at: null,
  last_error: null,
};
const plugs = [
  { ...base, id: 1, name: "Lamp", show_in_switchbar: true, energy_kwh: 1.5, last_state: "on", last_power_w: 12 },
  { ...base, id: 2, name: "Fan", show_in_switchbar: false, energy_kwh: 0.25, last_state: "off", last_power_w: null },
];

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation((path: string) =>
    Promise.resolve(path === "/plugs" ? plugs : path === "/features" ? { printer_enabled: true } : []),
  );
  postMock.mockResolvedValue(plugs[1]);
});

describe("PlugsPage", () => {
  it("lists plugs with energy and toggles one", async () => {
    wrap(<PlugsPage />);
    expect(await screen.findByText("Lamp")).toBeInTheDocument();
    expect(screen.getByText(/1\.75 kWh total/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Fan power" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/plugs/2/on"));
  });
});

describe("PlugSwitchbar", () => {
  it("shows only plugs flagged for the switchbar", async () => {
    wrap(<PlugSwitchbar />);
    expect(await screen.findByRole("switch", { name: "Lamp power" })).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Fan power" })).toBeNull();
  });
});
