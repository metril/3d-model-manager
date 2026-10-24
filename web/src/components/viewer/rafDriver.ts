/** Schedules R3F frames on the `requestAnimationFrame` of whichever window
 * currently hosts the canvas. R3F's own loop is bound to the window it was
 * created in; a canvas docked into a pop-out window would otherwise be driven
 * by the (possibly hidden/minimised, hence throttled) main window. */
export type RafWindow = Pick<Window, "requestAnimationFrame" | "cancelAnimationFrame">;

/** `advance` receives seconds from ONE window-independent monotonic clock
 * (`now`, the main realm's `performance.now()` by default). The rAF timestamp is
 * deliberately ignored: a pop-out window's timestamps count from its own
 * `timeOrigin`, so mixing them with the main window's (or with `advance`
 * calls elsewhere, e.g. screenshot capture) gives R3F a negative first delta. */
export function createRafScheduler(
  getWindow: () => RafWindow,
  advance: (seconds: number) => void,
  now: () => number = () => performance.now(),
) {
  let handle = 0;
  let scheduledOn: RafWindow | null = null;

  const cancel = () => {
    if (handle && scheduledOn) scheduledOn.cancelAnimationFrame(handle);
    handle = 0;
    scheduledOn = null;
  };

  /** Request a frame. Re-targets (cancel + reschedule) if the host window changed. */
  const invalidate = () => {
    const win = getWindow();
    if (handle && scheduledOn === win) return; // already queued on the right window
    cancel();
    scheduledOn = win;
    handle = win.requestAnimationFrame(() => {
      handle = 0;
      scheduledOn = null;
      advance(now() / 1000);
    });
  };

  return { invalidate, cancel };
}
