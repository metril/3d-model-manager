import { describe, expect, it } from "vitest";

import { getSelectedPlate, partUrl, plateLabel, selectablePlates, setSelectedPlate, viewerPlates } from "@/components/viewer/plates";
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
    expect(viewerPlates([file([plate(1)])])).toBeNull();
    expect(viewerPlates([file([plate(1, { has_glb: false }), plate(2, { has_glb: false })])])).toBeNull();
    expect(viewerPlates([file([plate(1), plate(2)])])?.blobHash).toBe("h1");
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
});
