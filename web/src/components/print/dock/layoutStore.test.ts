import type { SerializedDockview } from "dockview-react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LAYOUT_KEY,
  MODE_KEY,
  flushLayout,
  loadLayout,
  missingPanels,
  readDockMode,
  resetLayout,
  saveLayout,
  writeDockMode,
  writeLayout,
} from "./layoutStore";
import { PANEL_IDS } from "./panelMeta";

function leaf(id: string, views: string[]) {
  return { type: "leaf" as const, size: 100, data: { id, views, activeView: views[0] } };
}

function layout(): SerializedDockview {
  return {
    grid: {
      root: { type: "branch", data: [leaf("g1", ["printers", "camera"]), leaf("g2", ["queue"])], size: 100 },
      height: 800,
      width: 1200,
      orientation: "HORIZONTAL",
    },
    panels: { printers: { id: "printers" }, camera: { id: "camera" }, queue: { id: "queue" } },
  } as unknown as SerializedDockview;
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  resetLayout();
  vi.useRealTimers();
});

describe("print dashboard layoutStore", () => {
  it("round-trips a layout and reports registry panels unseen at save time", () => {
    writeLayout(layout());
    expect(loadLayout()?.layout.panels).toEqual(layout().panels);
    expect(loadLayout()?.unseen).toEqual([]);
    const stored = JSON.parse(localStorage.getItem(LAYOUT_KEY)!);
    stored.known = ["printers", "queue"];
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(stored));
    expect(loadLayout()?.unseen).toEqual(PANEL_IDS.filter((id) => id !== "printers" && id !== "queue"));
  });

  it("rejects absent, corrupt, wrong-version, unknown-panel and dangling-view layouts", () => {
    expect(loadLayout()).toBeNull();
    localStorage.setItem(LAYOUT_KEY, "{broken");
    expect(loadLayout()).toBeNull();
    const good = JSON.parse(JSON.stringify({ v: 1, known: [], layout: layout() }));
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ ...good, v: 99 }));
    expect(loadLayout()).toBeNull();
    const unknown = JSON.parse(JSON.stringify(good));
    unknown.layout.panels.bogus = { id: "bogus" };
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(unknown));
    expect(loadLayout()).toBeNull();
    const dangling = JSON.parse(JSON.stringify(good));
    delete dangling.layout.panels.queue;
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(dangling));
    expect(loadLayout()).toBeNull();
  });

  it("debounces saves to the last layout", () => {
    vi.useFakeTimers();
    saveLayout(layout());
    const second = layout();
    second.grid.width = 999;
    saveLayout(second);
    expect(localStorage.getItem(LAYOUT_KEY)).toBeNull();
    vi.advanceTimersByTime(300);
    expect(loadLayout()?.layout.grid.width).toBe(999);
  });

  it("flush writes immediately; reset clears storage and cancels a pending save", () => {
    saveLayout(layout());
    flushLayout();
    expect(loadLayout()).not.toBeNull();
    vi.useFakeTimers();
    saveLayout(layout());
    resetLayout();
    vi.advanceTimersByTime(1000);
    expect(localStorage.getItem(LAYOUT_KEY)).toBeNull();
  });

  it("remembers dock mode per browser; classic is the default", () => {
    expect(readDockMode()).toBe(false);
    writeDockMode(true);
    expect(localStorage.getItem(MODE_KEY)).toBe("1");
    expect(readDockMode()).toBe(true);
    writeDockMode(false);
    expect(readDockMode()).toBe(false);
  });

  it("missingPanels lists registry panels the dock does not host", () => {
    expect(missingPanels({ panels: [{ id: "printers" }, { id: "queue" }] } as never)).toEqual(
      PANEL_IDS.filter((id) => id !== "printers" && id !== "queue"),
    );
  });
});
