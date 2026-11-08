import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GcodePreview } from "@/components/viewer/GcodePreview";

const processGCode = vi.fn();
const renderSpy = vi.fn();
const dispose = vi.fn();
const initMock = vi.fn();

const fakePreview = {
  processGCode,
  render: renderSpy,
  dispose,
  maxLayerIndex: 2,
  endLayer: undefined as number | undefined,
  layers: [{ height: 0.2 }, { height: 0.4 }, { height: 0.6 }],
};

vi.mock("gcode-preview", () => ({
  init: (...args: unknown[]) => initMock(...args),
}));

const usePrintersMock = vi.fn();
const usePrinterStatusMock = vi.fn();

vi.mock("@/api/printers", () => ({
  usePrinters: (...args: unknown[]) => usePrintersMock(...args),
  usePrinterStatus: (...args: unknown[]) => usePrinterStatusMock(...args),
}));

describe("GcodePreview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    initMock.mockReturnValue(fakePreview);
    usePrintersMock.mockReturnValue({ data: [] });
    usePrinterStatusMock.mockReturnValue({ data: undefined });
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        headers: { get: () => null },
        text: () => Promise.resolve("G1 X0 Y0\n"),
      } as unknown as Response),
    ) as unknown as typeof fetch;
  });

  it("passes the plate param when the sliced plate is known", async () => {
    render(<GcodePreview fileId={42} plate={3} />);

    await waitFor(() => expect(processGCode).toHaveBeenCalled());
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(url).toBe("/api/files/42/download?member=gcode&plate=3");
  });

  it("fetches the file's embedded gcode via the member=gcode download param", async () => {
    render(<GcodePreview fileId={42} />);

    await waitFor(() => expect(processGCode).toHaveBeenCalledWith("G1 X0 Y0\n"));

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/files/42/download?member=gcode",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("shows the layer readout once loaded", async () => {
    render(<GcodePreview fileId={42} />);

    await waitFor(() => expect(screen.getByText(/Layer 3 \/ 3/)).toBeInTheDocument());
  });

  it("shows an error state when the fetch fails", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 404 } as Response)) as unknown as typeof fetch;

    render(<GcodePreview fileId={42} />);

    await waitFor(() =>
      expect(screen.getByText(/couldn't load the g-code preview/i)).toBeInTheDocument(),
    );
  });

  it("shows a too-large message instead of loading a huge gcode body", async () => {
    const text = vi.fn();
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        headers: { get: (name: string) => (name === "content-length" ? String(200 * 1024 * 1024) : null) },
        text,
      } as unknown as Response),
    ) as unknown as typeof fetch;

    render(<GcodePreview fileId={42} />);

    await waitFor(() =>
      expect(screen.getByText(/too large to preview in the browser/i)).toBeInTheDocument(),
    );
    expect(text).not.toHaveBeenCalled();
  });

  it("falls back to the default 256mm cube when there's no configured printer", async () => {
    usePrintersMock.mockReturnValue({ data: [] });

    render(<GcodePreview fileId={42} />);

    await waitFor(() =>
      expect(initMock).toHaveBeenCalledWith(
        expect.objectContaining({ buildVolume: { x: 256, y: 256, z: 256 } }),
      ),
    );
    const call = initMock.mock.calls[0][0];
    expect(call.extrusionColor).toBeUndefined();
  });

  it("derives build volume and extrusion color from the first printer/tray", async () => {
    usePrintersMock.mockReturnValue({
      data: [{ id: 7, build_volume_mm: { x: 300, y: 300, z: 400 } }],
    });
    usePrinterStatusMock.mockReturnValue({
      data: { trays: [{ slot: 0, color: null, material: null }, { slot: 1, color: "#FF0000", material: "PLA" }] },
    });

    render(<GcodePreview fileId={42} />);

    await waitFor(() =>
      expect(initMock).toHaveBeenCalledWith(
        expect.objectContaining({ buildVolume: { x: 300, y: 300, z: 400 }, extrusionColor: "#FF0000" }),
      ),
    );
  });

  it("lets an explicit buildVolume/extrusionColor prop override the derived values", async () => {
    usePrintersMock.mockReturnValue({
      data: [{ id: 7, build_volume_mm: { x: 300, y: 300, z: 400 } }],
    });

    render(<GcodePreview fileId={42} buildVolume={{ x: 100, y: 100, z: 100 }} extrusionColor="#00FF00" />);

    await waitFor(() =>
      expect(initMock).toHaveBeenCalledWith(
        expect.objectContaining({ buildVolume: { x: 100, y: 100, z: 100 }, extrusionColor: "#00FF00" }),
      ),
    );
  });
});
