import { describe, expect, it, vi } from "vitest";

import { createRafScheduler, type RafWindow } from "./rafDriver";

function fakeWindow() {
  let next = 1;
  const queue = new Map<number, FrameRequestCallback>();
  const win: RafWindow & { run: (ts: number) => void; pending: () => number } = {
    requestAnimationFrame: vi.fn((cb: FrameRequestCallback) => {
      queue.set(next, cb);
      return next++;
    }),
    cancelAnimationFrame: vi.fn((id: number) => void queue.delete(id)),
    run: (ts) => {
      const callbacks = [...queue.values()];
      queue.clear();
      callbacks.forEach((cb) => cb(ts));
    },
    pending: () => queue.size,
  };
  return win;
}

describe("createRafScheduler", () => {
  it("coalesces invalidations into one frame and advances in seconds", () => {
    const win = fakeWindow();
    const advance = vi.fn();
    const { invalidate } = createRafScheduler(() => win, advance, () => 2000);
    invalidate();
    invalidate();
    expect(win.pending()).toBe(1);
    win.run(0);
    expect(advance).toHaveBeenCalledExactlyOnceWith(2);
    invalidate();
    expect(win.pending()).toBe(1);
  });

  it("re-targets to the new window when the canvas moves", () => {
    const main = fakeWindow();
    const popout = fakeWindow();
    let host: RafWindow = main;
    const advance = vi.fn();
    const { invalidate } = createRafScheduler(() => host, advance, () => 1000);
    invalidate();
    host = popout;
    invalidate();
    expect(main.pending()).toBe(0);
    expect(popout.pending()).toBe(1);
    popout.run(0);
    expect(advance).toHaveBeenCalledWith(1);
  });

  it("cancel drops the queued frame", () => {
    const win = fakeWindow();
    const { invalidate, cancel } = createRafScheduler(() => win, vi.fn());
    invalidate();
    cancel();
    expect(win.pending()).toBe(0);
  });

  it("uses one monotonic clock across windows (popout rAF timestamps restart near 0)", () => {
    const main = fakeWindow();
    const popout = fakeWindow();
    let host: RafWindow = main;
    let clock = 500_000; // main-window performance.now(), ms
    const times: number[] = [];
    const { invalidate } = createRafScheduler(
      () => host,
      (seconds) => times.push(seconds),
      () => clock,
    );
    invalidate();
    clock += 16;
    main.run(clock);
    host = popout; // canvas moves to a window whose timeOrigin is much later
    invalidate();
    clock += 16;
    popout.run(0);
    invalidate();
    clock += 16;
    popout.run(16);
    expect(times).toHaveLength(3);
    const deltas = times.slice(1).map((t, i) => t - times[i]);
    expect(deltas.every((d) => d > 0 && d < 0.1)).toBe(true);
  });
});
