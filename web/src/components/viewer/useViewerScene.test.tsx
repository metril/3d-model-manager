import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ALL_FILES, getSelectedPlate, setActiveViewerBlob, setSelectedPlate, useActiveViewerBlob } from "@/components/viewer/plates";
import { useViewerScene } from "@/components/viewer/useViewerScene";
import type { FileOut, PlateOut } from "@/api/types";

vi.mock("@/api/printers", () => ({ usePrinters: () => ({ data: [] }) }));

const plate = (index: number): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: true, filaments: [],
  name: `P${index}`, has_glb: true,
});
const FILE = {
  id: 1, blob_hash: "vs1", format: "3mf", kind: "model", glb_preview_ready: false,
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
    const other = { ...FILE, id: 2, blob_hash: "vs2", format: "stl", meta: { triangle_count: 5 } } as unknown as FileOut;
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [BED_FILE, other], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    let sp = result.current.stageProps;
    expect(sp.layout?.beds).toHaveLength(2);
    expect(sp.parts.map((p) => [p.id, p.url, p.offset])).toEqual([
      [-1001, "/api/blobs/vs1/plates/1/glb?v=5", [0, 0, 0]],
      [-1002, "/api/blobs/vs1/plates/2/glb?v=5", [307.2, 0, 0]],
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
    expect(stageProps.parts[0].url).toBe("/api/blobs/vs1/glb?v=5");
    const fit0 = stageProps.fitSignal;

    act(() => result.current.stageProps.plateInfo!.onChange(2));
    expect(result.current.stageProps.parts[0].url).toBe("/api/blobs/vs1/plates/2/glb?v=5");
    expect(result.current.stageProps.fitSignal).toBe(fit0 + 1);

    unmount();
    expect(getSelectedPlate("vs1")).toBeNull();
  });

  it("drops the other files from the tree while a plate is isolated", () => {
    const other = { ...FILE, id: 2, blob_hash: "vs2", format: "stl", meta: { triangle_count: 5 } } as unknown as FileOut;
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [FILE, other], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    // default: the profile is isolated, "All files" restores the combined view
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1]);
    expect(result.current.stageProps.plateInfo!.hasAllFiles).toBe(true);
    act(() => result.current.stageProps.plateInfo!.onChange(2));
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1]);
    act(() => result.current.stageProps.plateInfo!.onFileChange!("*"));
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1, 2]);
    act(() => result.current.stageProps.plateInfo!.onFileChange!("vs1"));
    unmount();
  });

  it("a single-plate profile is offered and isolated on its whole-file GLB, no All files", () => {
    const single = { ...FILE, meta: { ...FILE.meta, plates: [plate(1)] } } as unknown as FileOut;
    const two = { ...single, id: 2, blob_hash: "vs2" } as unknown as FileOut;
    const { result, unmount } = renderHook(() => useViewerScene({ slug: "m", files: [single, two], persist: false, defaultAllChecked: true }), { wrapper });
    const info = result.current.stageProps.plateInfo!;
    expect(info.files).toHaveLength(2);
    expect(info.hasAllFiles).toBe(false);
    act(() => info.onFileChange!("vs2"));
    expect(result.current.stageProps.parts.map((p) => [p.id, p.url])).toEqual([[2, "/api/blobs/vs2/glb?v=5"]]);
    unmount();
    setSelectedPlate("vs1", null);
  });
});

describe("useViewerScene profile isolation", () => {
  const stl = { ...FILE, id: 2, blob_hash: "vs2", format: "stl", meta: { triangle_count: 5 } } as unknown as FileOut;

  it("colours, AMS sync and pop-outs act on the shown ids, and Parts is hidden", () => {
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [FILE, stl], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    expect(result.current.stageProps.profileActive).toBe(true);
    expect(result.current.stageProps.checkedList).toEqual([1]);
    expect([...result.current.stageProps.checkedIds]).toEqual([1]);
    act(() => result.current.stageProps.onSetAllColors("#112233"));
    expect(Object.keys(result.current.stageProps.colors)).toEqual(["1"]);
    act(() => result.current.stageProps.plateInfo!.onFileChange!(ALL_FILES));
    expect(result.current.stageProps.profileActive).toBe(false);
    expect(result.current.stageProps.checkedList).toEqual([1, 2]);
    unmount();
  });

  it("does not inherit All files left in the store by another model", () => {
    setActiveViewerBlob(ALL_FILES);
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "m", files: [FILE, stl], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    expect(result.current.stageProps.parts.map((p) => p.id)).toEqual([1]);
    unmount();
  });

  it("re-publishes the resolved profile when the store holds an unknown blob", () => {
    const { result, unmount } = renderHook(
      () => ({ scene: useViewerScene({ slug: "m", files: [FILE, stl], persist: false, defaultAllChecked: true }), active: useActiveViewerBlob() }),
      { wrapper },
    );
    act(() => setActiveViewerBlob("gone"));
    expect(result.current.scene.stageProps.plateInfo!.blobHash).toBe("vs1");
    expect(result.current.active).toBe("vs1");
    unmount();
  });
});

describe("useViewerScene pop-out seed + session memory", () => {
  beforeEach(() => sessionStorage.clear());

  it("seeds the pop-out plate before the first read and keeps ?ex= explode", () => {
    const seen: (number | null | undefined)[] = [];
    const { result, unmount } = renderHook(
      () => {
        const r = useViewerScene({ slug: "m", files: [FILE], persist: false, initial: { blob: "vs1", plate: 2, tools: { explode: 0.5 } } });
        seen.push(r.stageProps.plateInfo?.value);
        return r;
      },
      { wrapper },
    );
    expect(seen[0]).toBe(2);
    expect(new Set(seen)).toEqual(new Set([2]));
    expect(result.current.stageProps.tools.explode).toBe(0.5);
    unmount();
  });

  it("remembers profile + plate per model slug across a remount", () => {
    const first = renderHook(() => useViewerScene({ slug: "mem", files: [FILE] }), { wrapper });
    act(() => first.result.current.stageProps.plateInfo!.onChange(2));
    expect(JSON.parse(sessionStorage.getItem("viewer-profile:mem")!)).toEqual({ blob: "vs1", plate: 2 });
    first.unmount();
    const second = renderHook(() => useViewerScene({ slug: "mem", files: [FILE] }), { wrapper });
    expect(second.result.current.stageProps.plateInfo!.value).toBe(2);
    second.unmount();
    // a different model starts clean
    const other = renderHook(() => useViewerScene({ slug: "other", files: [FILE] }), { wrapper });
    expect(other.result.current.stageProps.plateInfo!.value).toBeNull();
    other.unmount();
  });

  it("isolates only plateInfo.fileId among same-blob duplicate files", () => {
    const dup = { ...FILE, id: 2 } as unknown as FileOut;
    const { result, unmount } = renderHook(
      () => useViewerScene({ slug: "dup", files: [FILE, dup], persist: false, defaultAllChecked: true }),
      { wrapper },
    );
    expect(result.current.stageProps.parts.map((p) => [p.id, p.visible])).toEqual([[1, true]]);
    unmount();
  });

  it("publishes no active blob when the model has no profiles", () => {
    const stl = { ...FILE, id: 3, blob_hash: "x", format: "stl", meta: { triangle_count: 5 } } as unknown as FileOut;
    setActiveViewerBlob("stale");
    const { result, unmount } = renderHook(
      () => ({ scene: useViewerScene({ slug: "np", files: [stl], persist: false }), active: useActiveViewerBlob() }),
      { wrapper },
    );
    expect(result.current.active).toBeNull();
    unmount();
  });
});
