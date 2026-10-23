import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { PlugOut } from "@/api/types";
import { PlugRow } from "@/components/print/PlugRow";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

const plug = (o: Partial<PlugOut> = {}): PlugOut => ({
  printer_id: 1,
  kind: "tasmota",
  config: {},
  auto_off_after_min: 10,
  auto_on_before_print: false,
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
  it("shows state/power/auto-off and toggles off", async () => {
    getMock.mockResolvedValue(plug());
    postMock.mockResolvedValue(plug({ last_state: "off" }));
    wrap(<PlugRow printerId={1} />);
    expect(await screen.findByText("85 W")).toBeInTheDocument();
    expect(screen.getByText(/auto-off 10 min/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Smart plug power" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/plug/off"));
    expect(await screen.findByText("Off")).toBeInTheDocument();
  });

  it("renders nothing when no plug (404)", async () => {
    getMock.mockRejectedValue(new ApiError(404, "no smart plug configured"));
    const { container } = wrap(<PlugRow printerId={1} />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
