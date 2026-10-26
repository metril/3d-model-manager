import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { PlugConfig } from "@/components/settings/PlugConfig";

const { getMock, putMock, postMock, patchMock, canBox } = vi.hoisted(() => ({
  getMock: vi.fn(),
  putMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  canBox: { current: true },
}));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ can: () => canBox.current }),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock, post: postMock, patch: patchMock } };
});

const stored = {
  id: 7,
  name: "Main",
  printer_id: 1,
  controls_printer_power: true,
  kind: "tasmota",
  config: { host: "10.0.0.5", password: "***" },
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
  last_state: null,
  last_power_w: null,
  last_polled_at: null,
  last_error: null,
};

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PlugConfig printerId={1} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  for (const m of [getMock, putMock, postMock, patchMock]) m.mockReset();
  canBox.current = true;
  getMock.mockImplementation((path: string) =>
    Promise.resolve(path === "/printers/1/plugs" ? [stored] : path === "/printers" ? [{ id: 1, name: "P1", power_off_when_drained: false }] : []),
  );
});

describe("PlugConfig", () => {
  it("is hidden without settings:write", () => {
    canBox.current = false;
    const { container } = wrap();
    expect(container).toBeEmptyDOMElement();
  });

  it("lists linked plugs and saves edits by id (masked secret kept)", async () => {
    putMock.mockResolvedValue(stored);
    wrap();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(await screen.findByDisplayValue("10.0.0.5")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Auto-off after print (minutes)"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plug" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith(
        "/plugs/7",
        expect.objectContaining({
          name: "Main",
          printer_id: 1,
          kind: "tasmota",
          config: { host: "10.0.0.5", password: "***" },
          auto_off: true,
          auto_off_after_min: 20,
        }),
      ),
    );
  });

  it("surfaces validation errors from save", async () => {
    putMock.mockRejectedValue(new ApiError(422, "entity not found"));
    wrap();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save plug" }));
    expect(await screen.findByText("entity not found")).toBeInTheDocument();
  });

  it("creates a plug pre-linked to the printer", async () => {
    postMock.mockResolvedValue(stored);
    wrap();
    fireEvent.click(await screen.findByRole("button", { name: "Add plug" }));
    fireEvent.change(await screen.findByLabelText("Host"), { target: { value: "10.0.0.9" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plug" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/plugs", expect.objectContaining({ printer_id: 1, config: { host: "10.0.0.9" } })),
    );
  });

  it("toggles power-off-when-drained on the printer", async () => {
    patchMock.mockResolvedValue({});
    wrap();
    fireEvent.click(await screen.findByRole("switch", { name: "Power off the printer when the queue drains" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/printers/1", { power_off_when_drained: true }));
  });
});
