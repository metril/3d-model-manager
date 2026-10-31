import { beforeEach, describe, expect, it } from "vitest";

import { buildManualRequest, loadLayout, saveLayout } from "@/components/plate/payload";
import {
  halfExtents,
  initialState,
  itemFits,
  MAX_ITEMS,
  plateReducer,
  type PlateAction,
  type PlateItem,
  type PlateState,
} from "@/components/plate/poseStore";

const item = (uid: string, over: Partial<PlateItem> = {}): PlateItem => ({
  uid,
  file_id: 1,
  x_mm: 50,
  y_mm: 50,
  rot_z_deg: 0,
  scale: 1,
  copy_index: 0,
  ...over,
});
const run = (state: PlateState, ...actions: PlateAction[]) => actions.reduce(plateReducer, state);
const start = () => run(initialState, { type: "init", items: [item("a")] });

describe("poseStore", () => {
  it("moves, rotates (wrapping) and clamps scale, each undoable", () => {
    let s = run(start(), { type: "patch", uid: "a", fields: { x_mm: 10.123456, y_mm: 20 } });
    expect(s.items[0]).toMatchObject({ x_mm: 10.123, y_mm: 20 });
    s = run(s, { type: "rotateBy", uid: "a", deg: 270 });
    expect(s.items[0].rot_z_deg).toBe(-90);
    s = run(s, { type: "patch", uid: "a", fields: { scale: 500 } });
    expect(s.items[0].scale).toBe(20);
    s = run(s, { type: "patch", uid: "a", fields: { scale: 0 } });
    expect(s.items[0].scale).toBe(0.01);
    expect(s.past).toHaveLength(4);
    s = run(s, { type: "undo" }, { type: "undo" }, { type: "undo" }, { type: "undo" });
    expect(s.items[0]).toMatchObject({ x_mm: 50, y_mm: 50, rot_z_deg: 0, scale: 1 });
    s = run(s, { type: "redo" });
    expect(s.items[0].x_mm).toBe(10.123);
  });

  it("silent patches (drag in progress) add no history; snapshot records one step", () => {
    let s = run(start(), { type: "snapshot" });
    s = run(
      s,
      { type: "patch", uid: "a", fields: { x_mm: 1 }, record: false },
      { type: "patch", uid: "a", fields: { x_mm: 2 }, record: false },
    );
    expect(s.past).toHaveLength(1);
    expect(plateReducer(s, { type: "undo" }).items[0].x_mm).toBe(50);
  });

  it("adds copies next to the last one, with the next copy_index, and removes them", () => {
    let s = run(start(), { type: "patch", uid: "a", fields: { rot_z_deg: 90, scale: 2 } }, { type: "addCopy", file_id: 1 });
    expect(s.items).toHaveLength(2);
    expect(s.items[1]).toMatchObject({ x_mm: 60, y_mm: 60, rot_z_deg: 90, scale: 2, copy_index: 1 });
    expect(s.selected).toBe(s.items[1].uid);
    s = run(s, { type: "removeCopy", uid: s.items[1].uid });
    expect(s.items.map((i) => i.uid)).toEqual(["a"]);
    expect(s.selected).toBeNull();
    s = run(s, { type: "addCopy", file_id: 1 }, { type: "removeLast", file_id: 1 });
    expect(s.items).toHaveLength(1);
  });

  it("caps the plate at MAX_ITEMS", () => {
    const items = Array.from({ length: MAX_ITEMS }, (_, i) => item(`u${i}`, { copy_index: i }));
    const s = run(initialState, { type: "init", items }, { type: "addCopy", file_id: 1 });
    expect(s.items).toHaveLength(MAX_ITEMS);
  });

  it("a new edit after undo drops the redo stack", () => {
    let s = run(start(), { type: "patch", uid: "a", fields: { x_mm: 1 } }, { type: "undo" });
    expect(s.future).toHaveLength(1);
    s = run(s, { type: "patch", uid: "a", fields: { x_mm: 2 } });
    expect(s.future).toHaveLength(0);
  });

  it("mirrors the server fit math (rotated footprint, scale, margin)", () => {
    const dims = { w_mm: 20, d_mm: 10, h_mm: 5 };
    expect(halfExtents(dims, item("a", { rot_z_deg: 90 })).map((v) => Math.round(v * 1e6) / 1e6)).toEqual([5, 10]);
    const bed = { x_mm: 100, y_mm: 100 };
    expect(itemFits(dims, item("a"), bed)).toBe(true);
    expect(itemFits(dims, item("a", { x_mm: 5 }), bed)).toBe(false);
    expect(itemFits(dims, item("a", { x_mm: 95, y_mm: 5, rot_z_deg: 90 }), bed)).toBe(false);
    expect(itemFits(dims, item("a", { x_mm: 50, y_mm: 50, rot_z_deg: 90 }), bed)).toBe(true);
    expect(itemFits(dims, item("a", { scale: 3 }), bed, 10)).toBe(false); // 15 mm tall > 10
    expect(itemFits(undefined, item("a", { x_mm: -999 }), bed)).toBe(true);
  });
});

describe("save payload", () => {
  beforeEach(() => localStorage.clear());

  it("builds a manual combine request with one item per copy", () => {
    const items = [item("a", { rot_z_deg: 90, scale: 2 }), item("b", { copy_index: 1, x_mm: 80 })];
    const req = buildManualRequest(items, { profileId: null, bed: { x_mm: 180, y_mm: 180 } }, "  hand  ");
    expect(req).toMatchObject({ layout: "manual", name: "hand", printer_profile_id: null, bed: { x_mm: 180, y_mm: 180 } });
    expect(req.items).toEqual([
      { file_id: 1, copies: 1, x_mm: 50, y_mm: 50, rot_z_deg: 90, scale: 2, copy_index: 0 },
      { file_id: 1, copies: 1, x_mm: 80, y_mm: 50, rot_z_deg: 0, scale: 1, copy_index: 1 },
    ]);
    const withProfile = buildManualRequest(items, { profileId: 7, bed: { x_mm: 1, y_mm: 1 } }, "");
    expect(withProfile).toMatchObject({ printer_profile_id: 7, bed: null, name: null });
  });

  it("persists the layout per model + file set and rejects foreign files", () => {
    const items = [item("a")];
    saveLayout("m", [2, 1], items);
    expect(loadLayout("m", [1, 2])).toEqual(items);
    expect(loadLayout("m", [1])).toBeNull();
    saveLayout("m", [9], [item("z", { file_id: 5 })]);
    expect(loadLayout("m", [9])).toBeNull();
  });
});
