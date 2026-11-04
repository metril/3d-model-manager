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

describe("useViewerScene plates", () => {
  it("swaps the part GLB to the plate endpoint, refits and clears on unmount", () => {
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [FILE], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    const { stageProps } = result.current;
    expect(stageProps.parts[0].url).toBe("/api/blobs/vs1/glb");
    const fit0 = stageProps.fitSignal;

    act(() => result.current.stageProps.plateInfo!.onChange(2));
    expect(result.current.stageProps.parts[0].url).toBe("/api/blobs/vs1/plates/2/glb");
    expect(result.current.stageProps.fitSignal).toBe(fit0 + 1);

    unmount();
    expect(getSelectedPlate("vs1")).toBeNull();
  });

  it("has no plate info for a single-plate file", () => {
    const single = { ...FILE, meta: { ...FILE.meta, plates: [plate(1)] } } as unknown as FileOut;
    const { result } = renderHook(() => useViewerScene({ slug: "m", files: [single], persist: false }), { wrapper });
    expect(result.current.stageProps.plateInfo).toBeNull();
    setSelectedPlate("vs1", null);
  });
});
