import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { boxesKey, fitDistance, canFit, pickFitDirection, isFlatScene, shouldResetView, createInteractionLod, createPoseGuard, createRefitScheduler, createViewState } from "./refitGuard";

const box = (v: number) => ({ min: { x: 0, y: 0, z: 0 }, max: { x: v, y: v, z: v } });

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("canFit", () => {
  it("rejects tiny panels and bad boxes", () => {
    expect(canFit({ width: 1, height: 400 }, box(1))).toBe(false);
    expect(canFit({ width: 400, height: 0 }, box(1))).toBe(false);
    expect(canFit({ width: 400, height: 400 }, box(NaN))).toBe(false);
    expect(canFit({ width: 400, height: 400 }, { min: { x: 1, y: 1, z: 1 }, max: { x: 0, y: 0, z: 0 } })).toBe(false);
  });
  it("accepts sane input", () => {
    expect(canFit({ width: 400, height: 300 }, box(2))).toBe(true);
    expect(canFit({ width: 400, height: 300 }, null)).toBe(true);
  });
});

describe("createRefitScheduler", () => {
  it("debounces resize fits and runs explicit ones immediately", () => {
    const run = vi.fn();
    const s = createRefitScheduler({ delay: 150, isGestureActive: () => false, run });
    s.request("debounced");
    s.request("debounced");
    vi.advanceTimersByTime(100);
    expect(run).not.toHaveBeenCalled();
    s.request("debounced");
    vi.advanceTimersByTime(149);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledExactlyOnceWith("debounced");
    s.request("now");
    expect(run).toHaveBeenCalledTimes(2);
  });
  it("holds fits during a gesture and flushes once at the end", () => {
    let down = true;
    const run = vi.fn();
    const s = createRefitScheduler({ delay: 150, isGestureActive: () => down, run });
    s.request("debounced");
    vi.advanceTimersByTime(200);
    s.request("now");
    expect(run).not.toHaveBeenCalled();
    down = false;
    s.gestureEnd();
    expect(run).toHaveBeenCalledExactlyOnceWith("now");
  });
});

describe("createPoseGuard", () => {
  it("restores the last finite pose", () => {
    const pose = { position: { x: 1, y: 2, z: 3 }, target: { x: 0, y: 0, z: 0 } };
    let cur = pose;
    const restore = vi.fn();
    const g = createPoseGuard(() => cur, restore);
    expect(g.check()).toBe(false);
    cur = { position: { x: NaN, y: 2, z: 3 }, target: pose.target };
    expect(g.check()).toBe(true);
    expect(restore).toHaveBeenCalledWith(pose);
  });
});

describe("createInteractionLod", () => {
  it("flips on at start and off 200ms after the last end", () => {
    const cb = vi.fn();
    const lod = createInteractionLod(200, cb);
    lod.start();
    expect(cb).toHaveBeenLastCalledWith(true);
    lod.end();
    vi.advanceTimersByTime(199);
    expect(cb).toHaveBeenCalledTimes(1);
    lod.start(); // a new gesture cancels the pending restore
    vi.advanceTimersByTime(500);
    expect(cb).toHaveBeenCalledTimes(1);
    lod.end();
    vi.advanceTimersByTime(200);
    expect(cb).toHaveBeenLastCalledWith(false);
    expect(cb).toHaveBeenCalledTimes(2);
  });
});

describe("boxesKey", () => {
  const box = (n: number) => ({ min: { x: 0, y: 0, z: 0 }, max: { x: n, y: n, z: n } });
  it("is stable for identical boxes", () => {
    expect(boxesKey([[1, box(20)]])).toBe(boxesKey([[1, box(20)]]));
  });
  it("changes when one part's geometry is swapped in place (same count)", () => {
    expect(boxesKey([[1, box(20)]])).not.toBe(boxesKey([[1, box(256)]]));
  });
  it("changes when the box only moves", () => {
    const moved = { min: { x: 5, y: 0, z: 0 }, max: { x: 25, y: 20, z: 20 } };
    expect(boxesKey([[1, box(20)]])).not.toBe(boxesKey([[1, moved]]));
  });
  it("distinguishes part ids", () => {
    expect(boxesKey([[1, box(20)]])).not.toBe(boxesKey([[2, box(20)]]));
  });
});

describe("fitDistance", () => {
  it("keeps the bounding sphere inside the view for a cube at a wide FOV", () => {
    const d = fitDistance({ x: 1, y: 1, z: 1 }, 75, 2, 1.2);
    const radius = Math.sqrt(3) / 2;
    expect(d * Math.sin((75 * Math.PI) / 360)).toBeGreaterThanOrEqual(radius * 1.2 - 1e-9);
    expect(d).toBeGreaterThan(1.035); // drei's longest-edge distance for this cube
  });
  it("uses the narrower horizontal fov on tall panels", () => {
    expect(fitDistance({ x: 1, y: 1, z: 1 }, 50, 0.5)).toBeGreaterThan(fitDistance({ x: 1, y: 1, z: 1 }, 50, 2));
  });
});


describe("pickFitDirection", () => {
  const flat = { x: 353, y: 4, z: 256 };
  const cube = { x: 47, y: 40, z: 47 };
  it("detects flat scenes", () => {
    expect(isFlatScene(flat)).toBe(true);
    expect(isFlatScene(cube)).toBe(false);
  });
  it("reset picks iso for solid, top-biased for flat", () => {
    expect(pickFitDirection({ current: { x: 0, y: 0, z: 5 }, size: cube, reset: true })).toEqual({ x: 1, y: 1, z: 1 });
    const d = pickFitDirection({ current: { x: 0, y: 0, z: 5 }, size: flat, reset: true })!;
    expect(d.y).toBeGreaterThan(d.x);
    expect(d.y).toBeGreaterThan(d.z);
  });
  it("keeps a solid scene's direction when not reset", () => {
    expect(pickFitDirection({ current: { x: 0, y: 0, z: 5 }, size: cube, reset: false })).toBeNull();
  });
  it("rescues flat edge-on, leaves flat elevated views", () => {
    expect(pickFitDirection({ current: { x: 0, y: 0.2, z: 5 }, size: flat, reset: false })).not.toBeNull();
    expect(pickFitDirection({ current: { x: 1, y: 1, z: 1 }, size: flat, reset: false })).toBeNull();
  });
});

describe("rescue gating", () => {
  const flat = { x: 353, y: 4, z: 256 };
  it("rescue:false leaves a flat edge-on view alone (presets, resize)", () => {
    expect(pickFitDirection({ current: { x: 0, y: 0, z: 5 }, size: flat, reset: false, rescue: false })).toBeNull();
  });
  it("shouldResetView only on key change", () => {
    expect(shouldResetView(null, "a")).toBe(true);
    expect(shouldResetView("a", "a")).toBe(false);
    expect(shouldResetView("a", "b")).toBe(true);
  });
});

describe("createViewState", () => {
  it("keeps the iso reset and the user pose pending until a fit is committed", () => {
    const v = createViewState();
    v.markInteracted();
    expect(v.wantReset("S:1", false)).toBe(true);
    // a fit that bailed (no commit) leaves both pending
    expect(v.wantReset("S:1", false)).toBe(true);
    expect(v.interacted).toBe(true);
    v.commit("S:1");
    expect(v.wantReset("S:1", false)).toBe(false);
    expect(v.interacted).toBe(false);
    expect(v.wantReset("S:2", false)).toBe(true);
  });
  it("never forces iso while a camera preset is active", () => {
    expect(createViewState().wantReset("S:1", true)).toBe(false);
  });
});

describe("createRefitScheduler held flush", () => {
  it("clears a held run once a run executes, so gestureEnd does not repeat it", () => {
    const run = vi.fn();
    let active = true;
    const s = createRefitScheduler({ delay: 100, isGestureActive: () => active, run });
    s.request("debounced");
    vi.advanceTimersByTime(100); // timer path fires while gesturing -> held
    expect(run).not.toHaveBeenCalled();
    active = false;
    s.request("now"); // runs immediately
    expect(run).toHaveBeenCalledTimes(1);
    s.gestureEnd();
    expect(run).toHaveBeenCalledTimes(1);
  });
});
