import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";

const handle = {
  mesh: {}, travLines: {}, setLayerRange: vi.fn(), setTravelVisible: vi.fn(), setColors: vi.fn(), dispose: vi.fn(),
};
vi.mock("@/lib/vendor/toolpathRenderer.js", () => ({
  buildSegmentData: vi.fn(() => ({ nV: 2, meta: { vType: new Uint8Array([1, 2]) } })),
  makeToolpath: vi.fn(() => handle),
  computeColors: vi.fn(() => ({ color: new Float32Array(8) })),
  TYPE_COLOR: { 1: [1, 0, 0] },
  DEFAULT_RANGES_COLORS: Array.from({ length: 11 }, () => [0, 0, 1]),
}));
vi.mock("three", async (orig) => {
  const actual = await orig<typeof import("three")>();
  class FakeRenderer {
    domElement = document.createElement("canvas");
    setSize = vi.fn(); setPixelRatio = vi.fn(); forceContextLoss = vi.fn(); render = vi.fn(); dispose = vi.fn();
  }
  return { ...actual, WebGLRenderer: FakeRenderer };
});
import GcodeToolpathViewer from "./GcodeToolpathViewer";

import sample from "../../test/fixtures/sample.gcode?raw";

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} unobserve() {} });
  vi.stubGlobal("requestAnimationFrame", () => 0);
  vi.stubGlobal("cancelAnimationFrame", () => {});
  handle.dispose.mockClear();
});

describe("GcodeToolpathViewer", () => {
  it("renders controls and stats on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(sample, { status: 200 })));
    const onLoaded = vi.fn();
    const { unmount } = render(<GcodeToolpathViewer gcodeUrl="/api/archives/1/gcode?plate=1" onLoaded={onLoaded} />);
    await screen.findByTestId("gcode-stats");
    expect(screen.getByLabelText("Top layer")).toBeTruthy();
    expect(screen.getByLabelText("Colour by")).toBeTruthy();
    expect(screen.getByText("Walls")).toBeTruthy();
    expect(screen.getByText(/Est\. 1h 2m/)).toBeTruthy();
    expect(onLoaded).toHaveBeenCalledWith(expect.objectContaining({ layers: expect.any(Number) }));
    await waitFor(() => expect(handle.setColors).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Colour by"), { target: { value: "speed" } });
    unmount();
    expect(handle.dispose).toHaveBeenCalled();
  });

  it("renders the empty state on 404", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })));
    render(<GcodeToolpathViewer gcodeUrl="/x" />);
    await screen.findByText("No G-code available for this item.");
  });

  it("renders retry on network error and refetches", async () => {
    const f = vi.fn().mockRejectedValueOnce(new Error("net")).mockResolvedValue(new Response(sample, { status: 200 }));
    vi.stubGlobal("fetch", f);
    render(<GcodeToolpathViewer gcodeUrl="/x" />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByTestId("gcode-stats");
    expect(f).toHaveBeenCalledTimes(2);
  });
});
