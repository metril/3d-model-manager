import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { boxesKey, fitDistance, canFit, createInteractionLod, createPoseGuard, createRefitScheduler } from "./refitGuard";

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
