import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { PlugConfig } from "@/components/settings/PlugConfig";

const { getMock, putMock, canBox } = vi.hoisted(() => ({
  getMock: vi.fn(),
  putMock: vi.fn(),
  canBox: { current: true },
}));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ can: () => canBox.current }),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PlugConfig printerId={1} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  getMock.mockReset();
  putMock.mockReset();
  canBox.current = true;
  getMock.mockRejectedValue(new ApiError(404, "none"));
});

describe("PlugConfig", () => {
  it("is hidden without settings:write", () => {
    canBox.current = false;
    const { container } = wrap();
    expect(container).toBeEmptyDOMElement();
  });

  it("loads a stored plug (masked secret) and saves changes", async () => {
    getMock.mockResolvedValue({
      printer_id: 1,
      kind: "tasmota",
      config: { host: "10.0.0.5", password: "***" },
      auto_off_after_min: 10,
      auto_on_before_print: false,
      last_state: null,
      last_power_w: null,
      last_polled_at: null,
      last_error: null,
    });
    putMock.mockResolvedValue({ printer_id: 1, kind: "tasmota", config: { host: "10.0.0.5", password: "***" }, auto_off_after_min: 20, auto_on_before_print: false, last_state: null, last_power_w: null, last_polled_at: null, last_error: null });
    wrap();
    expect(await screen.findByDisplayValue("10.0.0.5")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Auto-off after print (minutes)"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plug" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/printers/1/plug", {
        kind: "tasmota",
        config: { host: "10.0.0.5", password: "***" },
        auto_off_after_min: 20,
        auto_on_before_print: false,
      }),
    );
  });

  it("surfaces validation errors from save", async () => {
    getMock.mockResolvedValue({
      printer_id: 1, kind: "ha", config: { entity_id: "switch.x" }, auto_off_after_min: null,
      auto_on_before_print: false, last_state: null, last_power_w: null, last_polled_at: null, last_error: null,
    });
    putMock.mockRejectedValue(new ApiError(422, "entity not found"));
    wrap();
    fireEvent.click(await screen.findByRole("button", { name: "Save plug" }));
    expect(await screen.findByText("entity not found")).toBeInTheDocument();
  });
});

describe("PlugConfig refetch", () => {
  it("does not clobber in-progress edits when the plug query refetches", async () => {
    const stored = {
      printer_id: 1, kind: "tasmota", config: { host: "10.0.0.5" }, auto_off_after_min: 10,
      auto_on_before_print: false, last_state: null, last_power_w: null, last_polled_at: null, last_error: null,
    };
    getMock.mockResolvedValue(stored);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <PlugConfig printerId={1} />
      </QueryClientProvider>,
    );
    const input = await screen.findByDisplayValue("10.0.0.5");
    fireEvent.change(input, { target: { value: "edited" } });
    getMock.mockResolvedValue({ ...stored, config: { host: "other" } });
    await qc.refetchQueries({ queryKey: ["printers", 1, "plug"] });
    expect(screen.getByDisplayValue("edited")).toBeInTheDocument();
  });
});
