import { describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";

import { bedsBounds, buildPlateLayout, layoutPartId, clearSelectedPlate, getSelectedPlate, partUrl, plateLabel, resolveActive, selectablePlates, setSelectedPlate, viewerPlates, visibleParts, handlePlateKey, withSourceFallback, nextProfile, plateTotal, safeColour } from "@/components/viewer/plates";
import type { FileOut, PlateOut } from "@/api/types";

const T = i18n.t.bind(i18n);
const plate = (index: number, over: Partial<PlateOut> = {}): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: true, filaments: [],
  name: `P${index}`, has_glb: true, ...over,
});
const file = (plates: PlateOut[] | null): FileOut =>
  ({ id: 1, blob_hash: "h1", glb_preview_ready: false, meta: plates ? { plates, triangle_count: 10 } : null }) as unknown as unknown as FileOut;

describe("plates", () => {
  it("needs two plates to be selectable", () => {
    expect(selectablePlates([plate(1)])).toEqual([]);
    expect(selectablePlates(null)).toEqual([]);
    expect(selectablePlates([plate(1), plate(2)])).toHaveLength(2);
  });

  it("lists every profile 3MF, single-plate included", () => {
    const f3 = (over: object) => ({ ...file([plate(1)]), format: "3mf", kind: "model", ...over }) as unknown as unknown as FileOut;
    expect(viewerPlates([f3({})]).map((e) => e.blobHash)).toEqual(["h1"]);
    expect(viewerPlates([f3({ meta: { plates: null } })])).toEqual([]);
    expect(viewerPlates([f3({ id: 2, blob_hash: "h2", format: "stl" })])).toEqual([]);
  });

  it("swaps the part url to the plate endpoint", () => {
    const f = file([plate(1), plate(2)]);
    expect(partUrl(f, null)).toBe("/api/blobs/h1/glb?v=5");
    expect(partUrl(f, 2)).toBe("/api/blobs/h1/plates/2/glb?v=5");
    expect(partUrl(f, 9)).toBe("/api/blobs/h1/glb?v=5");
  });

  it("labels and stores the selection", () => {
    expect(plateLabel({ index: 2, name: "Lid" }, T)).toBe("Plate 2 · Lid");
    expect(plateLabel({ index: 2, name: "" }, T)).toBe("Plate 2");
    expect(plateLabel({ index: 1, name: "Plate 1" }, T)).toBe("Plate 1");
    expect(plateLabel({ index: 3, name: "plate 3" }, T)).toBe("Plate 3");
    expect(plateLabel({ index: 2, name: "Plate 3" }, T)).toBe("Plate 2 · Plate 3");
    setSelectedPlate("hx", 3);
    expect(getSelectedPlate("hx")).toBe(3);
    setSelectedPlate("hx", null);
    expect(getSelectedPlate("hx")).toBeNull();
  });

  it("follows the checked file and clears a stale selection", () => {
    const a = { ...file([plate(1), plate(2)]), id: 1, blob_hash: "ha", format: "3mf", kind: "model" } as unknown as FileOut;
    const b = { ...file([plate(1), plate(2)]), id: 2, blob_hash: "hb", format: "3mf", kind: "model" } as unknown as FileOut;
    expect(viewerPlates([a, b], new Set([2]))[0]?.blobHash).toBe("hb");
    expect(viewerPlates([a, b], new Set())[0]?.blobHash).toBe("ha");
    setSelectedPlate("ha", 2);
    clearSelectedPlate("ha");
    expect(getSelectedPlate("ha")).toBeNull();
  });
});

describe("multi-file plates + isolation", () => {
  const f = (id: number, hash: string, rel: string, n = 2): FileOut =>
    ({ id, blob_hash: hash, rel_path: rel, format: "3mf", kind: "model", meta: { plates: Array.from({ length: n }, (_, i) => plate(i + 1)) } }) as unknown as unknown as FileOut;
  const a = f(1, "ha", "x/Alpha-123456.3mf");
  const b = f(2, "hb", "Beta.3mf");
  const c = f(3, "hc", "Single.3mf", 1);

  it("lists every plated file, checked first, with clean labels", () => {
    const out = viewerPlates([a, b, c], new Set([2]));
    expect(out.map((e) => [e.blobHash, e.label])).toEqual([["hb", "Beta"], ["ha", "Alpha"], ["hc", "Single"]]);
    expect(resolveActive(out, "ha")?.blobHash).toBe("ha");
    expect(resolveActive(out, "gone")?.blobHash).toBe("hb");
    expect(resolveActive([], null)).toBeNull();
  });

  it("isolates the active profile; no active blob keeps the checked set", () => {
    const checked = new Set([1, 2, 3]);
    expect([...visibleParts([a, b, c], checked, null)]).toEqual([1, 2, 3]);
    expect([...visibleParts([a, b, c], checked, "ha")]).toEqual([1]);
    expect([...visibleParts([a, b, c], new Set([2]), "ha")]).toEqual([1]);
  });
});

describe("plate layout", () => {
  const bedPlate = (i: number, over: Partial<PlateOut> = {}) =>
    plate(i, { bed: [256, 256], origin: [(i - 1) * 307.2, 0], ...over });

  it("is null for legacy rows without bed/origin", () => {
    expect(buildPlateLayout([plate(1), plate(2)], null)).toBeNull();
    expect(buildPlateLayout([bedPlate(1), plate(2)], null)).toBeNull();
  });

  it("all plates: one bed + offset GLB part per plate at (ox, 0, -oy)", () => {
    const l = buildPlateLayout([bedPlate(1), bedPlate(2, { origin: [307.2, -307.2] })], null)!;
    expect(l.beds.map((b) => b.offset)).toEqual([[0, 0, 0], [307.2, 0, 307.2]]);
    expect(l.parts).toEqual([
      { plate: 1, offset: [0, 0, 0] },
      { plate: 2, offset: [307.2, 0, 307.2] },
    ]);
  });

  it("all plates without plate GLBs falls back to the full GLB at raw coords", () => {
    const l = buildPlateLayout([bedPlate(1, { has_glb: false }), bedPlate(2, { has_glb: false })], null)!;
    expect(l.parts).toEqual([{ plate: null, offset: [0, 0, 0] }]);
    expect(l.beds).toHaveLength(2);
  });

  it("selected plate: its bed alone, GLB bed-local", () => {
    const l = buildPlateLayout([bedPlate(1), bedPlate(2)], 2)!;
    expect(l.beds).toHaveLength(1);
    expect(l.beds[0].offset).toEqual([0, 0, 0]);
    expect(l.parts).toEqual([{ plate: 2, offset: [0, 0, 0] }]);
    const noGlb = buildPlateLayout([bedPlate(1), bedPlate(2, { has_glb: false })], 2)!;
    expect(noGlb.beds[0].offset).toEqual([307.2, 0, 0]);
    expect(noGlb.parts).toEqual([{ plate: null, offset: [0, 0, 0] }]);
  });

  it("bounds cover every bed (x right, z toward -Y)", () => {
    const l = buildPlateLayout([bedPlate(1), bedPlate(2, { bed: [100, 50], origin: [300, -400] })], null)!;
    expect(bedsBounds(l.beds)).toEqual({ min: [0, 0, -256], max: [400, 0, 400] });
    expect(bedsBounds([])).toBeNull();
  });

  it("synthetic part ids never collide with file ids", () => {
    expect(layoutPartId(7, null)).toBe(7);
    expect(layoutPartId(7, 2)).toBe(-7002);
  });
});

describe("plate hotkeys", () => {
  const plates = [plate(1), plate(2), plate(4)];
  const files = [
    { fileId: 1, blobHash: "a", label: "A", plates },
    { fileId: 2, blobHash: "b", label: "B", plates: [plate(1)] },
  ];
  const nav = (over = {}) => ({ blobHash: "a", plates, files, hasAllFiles: true, value: null as number | null, onChange: vi.fn(), onFileChange: vi.fn(), ...over });

  it("[ and ] step through All, 1, 2, 4 and clamp at the ends", () => {
    const n = nav();
    expect(handlePlateKey("]", n)).toBe(true);
    expect(n.onChange).toHaveBeenLastCalledWith(1);
    const m = nav({ value: 2 });
    handlePlateKey("]", m);
    expect(m.onChange).toHaveBeenLastCalledWith(4);
    handlePlateKey("[", m);
    expect(m.onChange).toHaveBeenLastCalledWith(1);
    const last = nav({ value: 4 });
    handlePlateKey("]", last);
    expect(last.onChange).toHaveBeenLastCalledWith(4);
    const first = nav({ value: null });
    handlePlateKey("[", first);
    expect(first.onChange).toHaveBeenLastCalledWith(null);
  });

  it("0 selects All plates; P cycles profiles incl. All files", () => {
    const n = nav({ value: 2 });
    expect(handlePlateKey("0", n)).toBe(true);
    expect(n.onChange).toHaveBeenCalledWith(null);
    handlePlateKey("p", n);
    expect(n.onFileChange).toHaveBeenLastCalledWith("b");
    expect(nextProfile(files, true, "b")).toBe("*");
    expect(nextProfile(files, true, "*")).toBe("a");
  });

  it("ignores other keys, missing nav, and single-plate profiles", () => {
    expect(handlePlateKey("x", nav())).toBe(false);
    expect(handlePlateKey("]", null)).toBe(false);
    const single = nav({ plates: [plate(1)] });
    expect(handlePlateKey("]", single)).toBe(false);
    expect(handlePlateKey("0", single)).toBe(false);
    expect(handlePlateKey("p", nav({ files: [files[0]], hasAllFiles: false }))).toBe(false);
  });
});

describe("plate helpers", () => {
  it("total is the max index; colours are validated; source fills time/weight", () => {
    expect(plateTotal([plate(1), plate(4)])).toBe(4);
    expect(safeColour("#00ff00")).toBe("#00ff00");
    expect(safeColour("#00ff00cc")).toBe("#00ff00cc");
    expect(safeColour("red; background:url(x)")).toBeUndefined();
    const src = { plates: [{ index: 1, name: null, thumb: null, prediction: 3600, weight: 12 }] };
    const out = withSourceFallback([plate(1), plate(2)], src);
    expect([out[0].prediction_s, out[0].weight_g, out[1].prediction_s]).toEqual([3600, 12, null]);
    const plates = [plate(1, { prediction_s: 5, weight_g: 6 })];
    expect(withSourceFallback(plates, src)).toBe(plates);
  });
  it("visibleParts limits isolation to the active file among same-blob duplicates", () => {
    const fs = [{ id: 1, blob_hash: "h" }, { id: 2, blob_hash: "h" }];
    expect([...visibleParts(fs, new Set(), "h", 2)]).toEqual([2]);
    expect([...visibleParts(fs, new Set(), "h")]).toEqual([1, 2]);
  });
});
