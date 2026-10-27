import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { canFit, createInteractionLod, createPoseGuard, createRefitScheduler } from "./refitGuard";

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
