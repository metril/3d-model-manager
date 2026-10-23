import { beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_PREFS, WIDGET_IDS, cycleSize, loadPrefs, normalize, reorder, savePrefs } from "@/lib/statsLayout";

beforeEach(() => localStorage.clear());

describe("statsLayout", () => {
  it("reorders by moving active to over's slot", () => {
    const r = reorder(DEFAULT_PREFS.widgets, "records", "quick");
    expect(r[0].id).toBe("records");
    expect(r.map((w) => w.id).sort()).toEqual([...WIDGET_IDS].sort());
    expect(reorder(DEFAULT_PREFS.widgets, "nope", "quick")).toBe(DEFAULT_PREFS.widgets);
  });
  it("cycles size 1 -> 2 -> 3 -> 1", () => {
    let w = DEFAULT_PREFS.widgets.map((x) => (x.id === "success" ? { ...x, size: 1 as const } : x));
    w = cycleSize(w, "success");
    expect(w.find((x) => x.id === "success")?.size).toBe(2);
    w = cycleSize(cycleSize(w, "success"), "success");
    expect(w.find((x) => x.id === "success")?.size).toBe(1);
  });
  it("normalizes junk and appends missing widgets", () => {
    const n = normalize({ widgets: [{ id: "records", size: 9 }, { id: "bogus", size: 1 }, { id: "records", size: 1 }], days: 5 });
    expect(n.widgets[0].id).toBe("records");
    expect(n.widgets).toHaveLength(WIDGET_IDS.length);
    expect(n.days).toBe(30);
  });
  it("persists per user", () => {
    savePrefs(1, { ...DEFAULT_PREFS, days: 90 });
    expect(loadPrefs(1).days).toBe(90);
    expect(loadPrefs(2).days).toBe(30);
  });
});
