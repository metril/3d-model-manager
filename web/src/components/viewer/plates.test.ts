import { describe, expect, it } from "vitest";

import { clearSelectedPlate, getSelectedPlate, partUrl, plateLabel, resolveActive, selectablePlates, setSelectedPlate, viewerPlates, visibleParts } from "@/components/viewer/plates";
import type { FileOut, PlateOut } from "@/api/types";

const plate = (index: number, over: Partial<PlateOut> = {}): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: true, filaments: [],
  name: `P${index}`, has_glb: true, ...over,
});
const file = (plates: PlateOut[] | null): FileOut =>
  ({ id: 1, blob_hash: "h1", glb_preview_ready: false, meta: plates ? { plates, triangle_count: 10 } : null }) as unknown as FileOut;

describe("plates", () => {
  it("needs two plates to be selectable", () => {
    expect(selectablePlates([plate(1)])).toEqual([]);
    expect(selectablePlates(null)).toEqual([]);
    expect(selectablePlates([plate(1), plate(2)])).toHaveLength(2);
  });

  it("finds the viewer plates only for multi-plate files with a GLB", () => {
    expect(viewerPlates([file([plate(1)])])).toEqual([]);
    expect(viewerPlates([file([plate(1, { has_glb: false }), plate(2, { has_glb: false })])])).toEqual([]);
    expect(viewerPlates([file([plate(1), plate(2)])])[0]?.blobHash).toBe("h1");
  });

  it("swaps the part url to the plate endpoint", () => {
    const f = file([plate(1), plate(2)]);
    expect(partUrl(f, null)).toBe("/api/blobs/h1/glb");
    expect(partUrl(f, 2)).toBe("/api/blobs/h1/plates/2/glb");
    expect(partUrl(f, 9)).toBe("/api/blobs/h1/glb");
  });

  it("labels and stores the selection", () => {
    expect(plateLabel({ index: 2, name: "Lid" })).toBe("Plate 2 · Lid");
    expect(plateLabel({ index: 2, name: "" })).toBe("Plate 2");
    setSelectedPlate("hx", 3);
    expect(getSelectedPlate("hx")).toBe(3);
    setSelectedPlate("hx", null);
    expect(getSelectedPlate("hx")).toBeNull();
  });

  it("follows the checked file and clears a stale selection", () => {
    const a = { ...file([plate(1), plate(2)]), id: 1, blob_hash: "ha" } as FileOut;
    const b = { ...file([plate(1), plate(2)]), id: 2, blob_hash: "hb" } as FileOut;
    expect(viewerPlates([a, b], new Set([2]))[0]?.blobHash).toBe("hb");
    expect(viewerPlates([a, b], new Set())[0]?.blobHash).toBe("ha");
    setSelectedPlate("ha", 2);
    clearSelectedPlate("ha");
    expect(getSelectedPlate("ha")).toBeNull();
  });
});

describe("multi-file plates + isolation", () => {
  const f = (id: number, hash: string, rel: string, n = 2): FileOut =>
    ({ id, blob_hash: hash, rel_path: rel, meta: { plates: Array.from({ length: n }, (_, i) => plate(i + 1)) } }) as unknown as FileOut;
  const a = f(1, "ha", "x/Alpha-123456.3mf");
  const b = f(2, "hb", "Beta.3mf");
  const c = f(3, "hc", "Single.3mf", 1);

  it("lists every plated file, checked first, with clean labels", () => {
    const out = viewerPlates([a, b, c], new Set([2]));
    expect(out.map((e) => [e.blobHash, e.label])).toEqual([["hb", "Beta"], ["ha", "Alpha"]]);
    expect(resolveActive(out, "ha")?.blobHash).toBe("ha");
    expect(resolveActive(out, "gone")?.blobHash).toBe("hb");
    expect(resolveActive([], null)).toBeNull();
  });

  it("isolates the active file once a plate is selected", () => {
    const checked = new Set([1, 2, 3]);
    expect([...visibleParts([a, b, c], checked, "ha", null)]).toEqual([1, 2, 3]);
    expect([...visibleParts([a, b, c], checked, "ha", 2)]).toEqual([1]);
    expect([...visibleParts([a, b, c], new Set([2]), "ha", 1)]).toEqual([1]);
  });
});
