import type { SerializedDockview } from "dockview-react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LAYOUT_KEY,
  cancelPendingSave,
  loadLayout,
  missingPanels,
  parseLayout,
  resetLayout,
  saveLayout,
  stripPopouts,
  writeLayout,
} from "./layoutStore";
import { PANEL_IDS } from "./panelMeta";

function leaf(id: string, views: string[]) {
  return { type: "leaf" as const, size: 100, data: { id, views, activeView: views[0] } };
}

function layout(overrides: Partial<SerializedDockview> = {}): SerializedDockview {
  return {
    grid: {
      root: {
        type: "branch",
        data: [leaf("g1", ["viewer", "notes"]), leaf("g2", ["specs"])],
        size: 100,
      },
      height: 800,
      width: 1200,
      orientation: "HORIZONTAL" as SerializedDockview["grid"]["orientation"],
    },
    panels: { viewer: { id: "viewer" }, notes: { id: "notes" }, specs: { id: "specs" } },
    ...overrides,
  } as SerializedDockview;
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  cancelPendingSave();
  vi.useRealTimers();
});

describe("layoutStore", () => {
  it("round-trips a layout and reports panels added to the registry since", () => {
    writeLayout(layout());
    const loaded = loadLayout();
    expect(loaded?.layout.panels).toEqual(layout().panels);
    // saved with the current registry as `known`, so nothing is unseen
    expect(loaded?.unseen).toEqual([]);
  });

  it.each(["orcaslicer", "studio"])("strips the retired %s panel from saved layouts", (retired) => {
    const saved = layout({
      panels: { viewer: { id: "viewer" }, [retired]: { id: retired } } as unknown as SerializedDockview["panels"],
    });
    (saved.grid.root as unknown as { data: unknown[] }).data = [
      leaf("g1", ["viewer"]),
      leaf("g2", [retired]),
      leaf("g3", ["viewer", retired]),
    ];
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ v: 2, known: ["viewer", retired], layout: saved }));
    const loaded = loadLayout();
    expect(loaded).not.toBeNull();
    expect(Object.keys(loaded?.layout.panels ?? {})).toEqual(["viewer"]);
    const json = JSON.stringify(loaded?.layout);
    expect(json).not.toContain(retired);
    expect((loaded!.layout.grid.root as unknown as { data: unknown[] }).data).toHaveLength(2);
  });

  it("prunes retired panels from floating groups and drops emptied ones", () => {
    const saved = layout({
      floatingGroups: [
        { data: { id: "f1", views: ["orcaslicer"], activeView: "orcaslicer" }, position: { top: 0, left: 0, width: 1, height: 1 } },
        { data: { id: "f2", views: ["notes", "studio"], activeView: "studio" }, position: { top: 0, left: 0, width: 1, height: 1 } },
      ],
    } as unknown as Partial<SerializedDockview>);
    (saved.panels as Record<string, unknown>).orcaslicer = { id: "orcaslicer" };
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ v: 2, known: ["viewer"], layout: saved }));
    const floating = loadLayout()?.layout.floatingGroups as unknown as Array<{ data: { views: string[]; activeView: string } }>;
    expect(floating).toHaveLength(1);
    expect(floating[0].data).toMatchObject({ views: ["notes"], activeView: "notes" });
  });

  it("discards a layout whose grid is left empty so the default is used", () => {
    const saved = layout({ panels: { orcaslicer: { id: "orcaslicer" } } as unknown as SerializedDockview["panels"] });
    (saved.grid.root as unknown as { data: unknown[] }).data = [leaf("g1", ["orcaslicer"])];
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ v: 2, known: ["orcaslicer"], layout: saved }));
    expect(loadLayout()).toBeNull();
  });

  it("flags registry panels that were unknown at save time", () => {
    localStorage.setItem(
      LAYOUT_KEY,
      JSON.stringify({ v: 2, known: ["viewer", "notes", "specs"], layout: layout() }),
    );
    expect(loadLayout()?.unseen).toEqual(PANEL_IDS.filter((id) => !["viewer", "notes", "specs"].includes(id)));
  });

  it("ignores a layout saved under the v1 key", () => {
    expect(LAYOUT_KEY).toMatch(/v4$/);
    const storage = { getItem: (k: string) => (k === "tdmm.modelDetailLayout.v1" ? "{}" : null) } as Storage;
    expect(loadLayout(storage)).toBeNull();
  });

  it("rejects wrong version, corrupt JSON and unknown panel ids", () => {
    expect(parseLayout(null)).toBeNull();
    expect(parseLayout("{nope")).toBeNull();
    expect(parseLayout(JSON.stringify({ v: 1, layout: layout() }))).toBeNull();
    expect(parseLayout(JSON.stringify({ v: 2, layout: { panels: {} } }))).toBeNull();
    const unknown = layout({ panels: { viewer: { id: "viewer" }, ghost: { id: "ghost" } } as never });
    expect(parseLayout(JSON.stringify({ v: 2, layout: unknown }))).toBeNull();
  });

  it("rejects a group that references a panel missing from `panels`", () => {
    const bad = layout({ panels: { viewer: { id: "viewer" } } as never });
    expect(parseLayout(JSON.stringify({ v: 2, layout: bad }))).toBeNull();
  });

  it("folds popout groups back into their reference group", () => {
    const withPopout = layout({
      popoutGroups: [
        { data: { id: "g3", views: ["specs"], activeView: "specs" }, gridReferenceGroup: "g2", position: {} },
      ] as never,
      grid: {
        ...layout().grid,
        root: { type: "branch", data: [leaf("g1", ["viewer", "notes"]), leaf("g2", [])], size: 100 },
      },
    });
    const out = stripPopouts(withPopout);
    expect(out.popoutGroups).toBeUndefined();
    const data = (out.grid.root.data as { data: { id: string; views: string[] } }[]).map((n) => n.data);
    expect(data.find((g) => g.id === "g2")?.views).toEqual(["specs"]);
  });

  it("falls back to the first docked group when the reference group is gone", () => {
    const withPopout = layout({
      popoutGroups: [{ data: { id: "g9", views: ["specs"] }, gridReferenceGroup: "missing", position: {} }] as never,
      grid: { ...layout().grid, root: leaf("g1", ["viewer", "notes"]) as never },
    });
    const out = stripPopouts(withPopout);
    expect((out.grid.root.data as { views: string[] }).views).toEqual(["viewer", "notes", "specs"]);
  });

  it("un-hides the placeholder group dockview keeps for a popped-out group", () => {
    const hidden = { ...leaf("g2", ["specs"]), visible: false };
    const withPopout = layout({
      popoutGroups: [{ data: { id: "g2", views: ["specs"] }, gridReferenceGroup: "g2", position: {} }] as never,
      grid: { ...layout().grid, root: { type: "branch", data: [leaf("g1", ["viewer", "notes"]), hidden], size: 100 } },
    });
    const out = stripPopouts(withPopout);
    const nodes = out.grid.root.data as { visible?: boolean; data: { views: string[]; activeView?: string } }[];
    expect(nodes[1].visible).toBe(true);
    expect(nodes[1].data.views).toEqual(["specs"]); // not duplicated
    expect(nodes[1].data.activeView).toBe("specs");
  });

  it("writeLayout never persists popout groups", () => {
    writeLayout(
      layout({ popoutGroups: [{ data: { id: "g3", views: [] }, position: {} }] as never }),
    );
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)!).layout.popoutGroups).toBeUndefined();
  });

  it("debounces saves to the last layout", () => {
    vi.useFakeTimers();
    saveLayout(layout());
    saveLayout(layout({ activeGroup: "g2" }));
    expect(localStorage.getItem(LAYOUT_KEY)).toBeNull();
    vi.advanceTimersByTime(300);
    expect(loadLayout()?.layout.activeGroup).toBe("g2");
  });

  it("reset clears storage and cancels a pending save", () => {
    vi.useFakeTimers();
    writeLayout(layout());
    saveLayout(layout());
    resetLayout();
    vi.advanceTimersByTime(500);
    expect(localStorage.getItem(LAYOUT_KEY)).toBeNull();
  });

  it("missingPanels lists registry panels the dock does not host", () => {
    const api = { panels: [{ id: "viewer" }, { id: "notes" }] } as never;
    expect(missingPanels(api)).toEqual(PANEL_IDS.filter((id) => id !== "viewer" && id !== "notes"));
  });
});
