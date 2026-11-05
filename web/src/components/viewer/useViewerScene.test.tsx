import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { getSelectedPlate, setSelectedPlate } from "@/components/viewer/plates";
import { useViewerScene } from "@/components/viewer/useViewerScene";
import type { FileOut, PlateOut } from "@/api/types";

vi.mock("@/api/printers", () => ({ usePrinters: () => ({ data: [] }) }));

const plate = (index: number): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: true, filaments: [],
  name: `P${index}`, has_glb: true,
});
const FILE = {
  id: 1, blob_hash: "vs1", glb_preview_ready: false,
  meta: { triangle_count: 10, plates: [plate(1), plate(2)] },
} as unknown as FileOut;

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

const BED_FILE = {
  ...FILE,
  meta: {
    triangle_count: 10,
    plates: [
      { ...plate(1), bed: [256, 256], origin: [0, 0] },
      { ...plate(2), bed: [256, 256], origin: [307.2, 0] },
    ],
  },
} as unknown as FileOut;

describe("useViewerScene plate layout", () => {
  it("All plates: one offset part per plate, other files dropped; plate: bed-local", () => {
    const other = { ...FILE, id: 2, blob_hash: "vs2", meta: { triangle_count: 5 } } as unknown as FileOut;
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [BED_FILE, other], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    let sp = result.current.stageProps;
    expect(sp.layout?.beds).toHaveLength(2);
    expect(sp.parts.map((p) => [p.id, p.url, p.offset])).toEqual([
      [-1001, "/api/blobs/vs1/plates/1/glb?v=2", [0, 0, 0]],
      [-1002, "/api/blobs/vs1/plates/2/glb?v=2", [307.2, 0, 0]],
    ]);
    act(() => result.current.stageProps.plateInfo!.onChange(2));
    sp = result.current.stageProps;
    expect(sp.layout?.beds).toHaveLength(1);
    expect(sp.parts.map((p) => [p.id, p.offset])).toEqual([[-1002, [0, 0, 0]]]);
    act(() => result.current.stageProps.plateInfo!.onChange(null));
    unmount();
  });

  it("legacy plates without bed keep the single-grid behaviour", () => {
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [FILE], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    expect(result.current.stageProps.layout).toBeNull();
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1]);
    unmount();
  });
});

describe("useViewerScene plates", () => {
  it("swaps the part GLB to the plate endpoint, refits and clears on unmount", () => {
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [FILE], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    const { stageProps } = result.current;
    expect(stageProps.parts[0].url).toBe("/api/blobs/vs1/glb?v=2");
    const fit0 = stageProps.fitSignal;

    act(() => result.current.stageProps.plateInfo!.onChange(2));
    expect(result.current.stageProps.parts[0].url).toBe("/api/blobs/vs1/plates/2/glb?v=2");
    expect(result.current.stageProps.fitSignal).toBe(fit0 + 1);

    unmount();
    expect(getSelectedPlate("vs1")).toBeNull();
  });

  it("drops the other files from the tree while a plate is isolated", () => {
    const other = { ...FILE, id: 2, blob_hash: "vs2", meta: { triangle_count: 5 } } as unknown as FileOut;
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [FILE, other], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1, 2]);
    act(() => result.current.stageProps.plateInfo!.onChange(2));
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1]);
    act(() => result.current.stageProps.plateInfo!.onChange(null));
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1, 2]);
    unmount();
  });

  it("has no plate info for a single-plate file", () => {
    const single = { ...FILE, meta: { ...FILE.meta, plates: [plate(1)] } } as unknown as FileOut;
    const { result } = renderHook(() => useViewerScene({ slug: "m", files: [single], persist: false }), { wrapper });
    expect(result.current.stageProps.plateInfo).toBeNull();
    setSelectedPlate("vs1", null);
  });
});
