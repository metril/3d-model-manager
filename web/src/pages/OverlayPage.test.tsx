import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OverlayView } from "@/pages/OverlayPage";

afterEach(() => vi.unstubAllGlobals());

function renderOverlay(token: string | undefined) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OverlayView id={3} token={token} />
    </QueryClientProvider>,
  );
}

describe("OverlayView", () => {
  it("renders the status strip from the public overlay route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          printer_name: "A1 Mini",
          gcode_state: "RUNNING",
          progress: 42,
          layer: 10,
          total_layers: 100,
          remaining_min: 65,
          nozzle_temp: 210,
          bed_temp: 60,
          subtask_name: "benchy",
          rotation: 0,
          stream_url: "/api/printers/3/camera/stream?token=ov_x",
          snapshot_url: "/api/printers/3/camera/snapshot?token=ov_x",
        }),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderOverlay("ov_x");

    const strip = await screen.findByTestId("overlay-strip");
    expect(strip).toHaveTextContent("A1 Mini");
    expect(strip).toHaveTextContent("RUNNING");
    expect(strip).toHaveTextContent("42%");
    expect(strip).toHaveTextContent("Layer 10/100");
    expect(strip).toHaveTextContent("ETA 1h 05m");
    expect(fetchMock).toHaveBeenCalledWith("/api/overlay/3?token=ov_x");
    expect(await screen.findByRole("img", { name: "Camera feed" })).toHaveAttribute(
      "src",
      "/api/printers/3/camera/stream?token=ov_x",
    );
    expect(document.body.style.background).toBe("transparent");
  });

  it("reports a missing token without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderOverlay(undefined);
    expect(screen.getByText("Missing overlay token")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
